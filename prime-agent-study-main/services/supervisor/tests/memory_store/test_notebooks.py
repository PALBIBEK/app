"""Notebooks in the memory service, through the client the supervisor uses.

Auth is the production path: the service verifies a tenant JWT, minted exactly
as the supervisor's client mints it.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
import pytest_asyncio

from supervisor.memory.client import MemoryRefused, MemoryUnavailable, NotebookClient
from supervisor.memory_service.notebooks.errors import (
    MemoryFactsMissing,
    NotebookExists,
    NotebookNotFound,
)
from supervisor.memory_service.notebooks.types import (
    CaptureMetadata,
    Edit,
    Sensitivity,
    personal_source,
    scope_source,
)
from supervisor.seams.services.memory_service_client import memory_service_credential
from tests.support.notebooks import MemoryServiceHandle, memory_service

pytestmark = pytest.mark.asyncio

SECRET = "notebook-tests-service-jwt-secret!!"
PIN = "composition:githi@sha256:abc"
TRACKED = CaptureMetadata(sensitivity=Sensitivity.ORDINARY, inherited=())


@pytest_asyncio.fixture
async def svc(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[MemoryServiceHandle]:
    monkeypatch.setenv("SERVICE_JWT_SECRET", SECRET)  # the service verifies, the client mints
    monkeypatch.delenv("MEMORY_SERVICE_JWT_SECRET", raising=False)
    monkeypatch.delenv("MEMORY_SERVICE_TOKEN", raising=False)
    async with memory_service(f"sqlite+aiosqlite:///{tmp_path / 'memory.db'}") as handle:
        yield handle


def _raw(svc: MemoryServiceHandle, tenant: str) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=svc.app),
        base_url="http://memory.test",
        headers={"Authorization": f"Bearer {memory_service_credential(tenant)}"},
    )


async def test_a_notebook_id_names_its_tenant_and_another_tenant_cannot_reach_it(
    svc: MemoryServiceHandle,
) -> None:
    notebooks = svc.client()
    nb = await notebooks.ensure(tenant_id="t1", owner="alice", agent_pin=PIN, name="default")
    assert nb.id.startswith("t1~")
    async with _raw(svc, "t2") as other:
        assert (await other.get(f"/v1/notebooks/{nb.id}")).status_code == 404
        _, _, hex_part = nb.id.partition("~")
        assert (await other.get(f"/v1/notebooks/t2~{hex_part}")).status_code == 404  # forged prefix
        key = {"tenant_id": "t1", "owner": "mallory", "agent_pin": PIN, "name": "x"}
        assert (await other.post("/v1/notebooks", json=key)).status_code == 403
        listing = await other.get(
            "/v1/notebooks", params={"tenant_id": "t1", "owner": "alice", "agent_pin": PIN}
        )
        assert listing.status_code == 403


async def test_the_service_errors_reach_the_supervisor_as_its_own_classes(
    svc: MemoryServiceHandle,
) -> None:
    notebooks = svc.client()
    nb = await notebooks.create(tenant_id="t1", owner="alice", agent_pin=PIN, name="work")
    with pytest.raises(NotebookExists):
        await notebooks.create(tenant_id="t1", owner="alice", agent_pin=PIN, name="work")
    with pytest.raises(NotebookNotFound):
        await notebooks.get("t1~0000")
    with pytest.raises(NotebookNotFound):
        await notebooks.get("no-tenant-in-this-id")
    await notebooks.capture(nb.id, ["Prefers tea"], context=TRACKED)
    with pytest.raises(MemoryFactsMissing) as missing:
        await notebooks.edit(nb.id, [Edit("999", "nope")])
    assert missing.value.ids == ("999",)
    assert [f.text for f in await notebooks.facts(nb.id)] == ["Prefers tea"], "all or nothing"


async def test_a_wrong_credential_is_refused_and_an_absent_service_is_unavailable(
    svc: MemoryServiceHandle,
) -> None:
    forged = NotebookClient(
        "http://memory.test",
        transport=httpx.ASGITransport(app=svc.app),
        credential=lambda tenant: "not-a-valid-jwt",
    )
    with pytest.raises(MemoryRefused):
        await forged.ensure(tenant_id="t1", owner="alice", agent_pin=PIN, name="default")
    await forged.aclose()
    gone = NotebookClient("http://127.0.0.1:9", timeout_s=2)  # nothing listens on the discard port
    with pytest.raises(MemoryUnavailable) as unavailable:
        await gone.ensure(tenant_id="t1", owner="alice", agent_pin=PIN, name="default")
    assert unavailable.value.retryable
    await gone.aclose()


async def test_history_and_restore_round_trip_through_the_service(svc: MemoryServiceHandle) -> None:
    notebooks = svc.client()
    nb = await notebooks.ensure(tenant_id="t1", owner="alice", agent_pin=PIN, name="default")
    await notebooks.capture(nb.id, ["Prefers tea", "Lives in Pune"], context=TRACKED)
    before = (await notebooks.get(nb.id)).revision
    await notebooks.clear(nb.id)
    assert await notebooks.facts(nb.id) == []
    assert sorted(f.text for f in await notebooks.facts_at(nb.id, before)) == [
        "Lives in Pune",
        "Prefers tea",
    ]
    await notebooks.restore(nb.id, before)
    assert sorted(f.text for f in await notebooks.facts(nb.id)) == ["Lives in Pune", "Prefers tea"]
    ops = [r.op for r in await notebooks.revisions(nb.id)]
    assert ops.count("insert") == 2 and "delete" in ops
    assert (await notebooks.stats(nb.id)).facts == 2


async def _bank_recall(svc: MemoryServiceHandle, owner: str, agent: str, query: str) -> list[str]:
    async with _raw(svc, "t1") as raw:
        response = await raw.post(
            "/v1/memory/recall",
            json={
                "scope": {"tenant": "t1", "subject": f"user:{owner}", "namespace": agent},
                "query": query,
                "max_tokens": 512,
            },
        )
    assert response.status_code == 200, response.text
    return [item["content"] for item in response.json()["items"]]


async def test_only_facts_safe_to_recall_without_audience_rules_reach_the_bank(
    svc: MemoryServiceHandle,
) -> None:
    """The bank's recall applies no audience rules: only the owner's ordinary facts go there."""
    notebooks = svc.client()
    nb = await notebooks.ensure(
        tenant_id="t1", owner="alice", agent_pin=PIN, name="default", agent="githi"
    )
    home = CaptureMetadata(
        source=personal_source("alice"), sensitivity=Sensitivity.ORDINARY, inherited=()
    )
    await notebooks.capture(nb.id, ["Prefers green tea in the morning"], context=home)
    shared = CaptureMetadata(
        source=scope_source("project-x"), sensitivity=Sensitivity.ORDINARY, inherited=()
    )
    await notebooks.capture(nb.id, ["Project codename is Kiwi tea"], context=shared)
    secret = CaptureMetadata(sensitivity=Sensitivity.SENSITIVE, inherited=())
    await notebooks.capture(nb.id, ["Takes tea for a medical condition"], context=secret)

    recalled = await _bank_recall(svc, "alice", "githi", "tea")
    assert recalled == ["Prefers green tea in the morning"]

    (fact,) = [f for f in await notebooks.facts(nb.id) if f.text.startswith("Prefers")]
    await notebooks.delete(nb.id, [fact.id])
    assert await _bank_recall(svc, "alice", "githi", "tea") == [], "a deleted fact leaves the bank"


async def test_erasing_a_user_takes_their_notebooks_and_history(svc: MemoryServiceHandle) -> None:
    notebooks = svc.client()
    nb = await notebooks.ensure(tenant_id="t1", owner="alice", agent_pin=PIN, name="default")
    await notebooks.capture(nb.id, ["Prefers tea"], context=TRACKED)
    keep = await notebooks.ensure(tenant_id="t1", owner="bob", agent_pin=PIN, name="default")
    async with _raw(svc, "t1") as raw:
        erased = await raw.post(
            "/v1/memory/erase_subject",
            json={"tenant": "t1", "subject": "user:alice", "actor": "dpo", "reason": "request"},
        )
    assert erased.status_code == 200, erased.text
    assert erased.json().get("notebooks_erased") == 1
    with pytest.raises(NotebookNotFound):
        await notebooks.get(nb.id)
    assert (await notebooks.get(keep.id)).owner == "bob"
