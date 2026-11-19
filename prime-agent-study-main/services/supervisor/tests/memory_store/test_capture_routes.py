"""The capture queue over HTTP (G3), through the client the supervisor uses, with the
production auth path: the service verifies tenant JWTs minted as the client mints them.

A job, a lease and a session answer only their own tenant; the claimable-tenants listing is
the one cross-tenant read, and it carries no content.
"""

from __future__ import annotations

import dataclasses
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
import pytest_asyncio

from supervisor.memory.client import NotebookClient
from supervisor.memory_service.capture.errors import (
    CaptureConflict,
    CaptureInvalid,
    StaleLease,
    UnknownCaptureSession,
)
from supervisor.memory_service.notebooks.types import Sensitivity
from supervisor.memory_service.notebooks.wire import observation_out
from supervisor.memory_service.observations import PROMPT_VERSION, ObservationDraft, ObservationType
from supervisor.seams.services.memory_service_client import memory_service_credential
from tests.support.notebooks import BACKENDS, MemoryServiceHandle, _database, memory_service

pytestmark = pytest.mark.asyncio

SECRET = "capture-route-tests-service-jwt-secret!!"
PIN = "composition:githi@sha256:capture-routes"
NOOP = {"observations": [], "retire": []}


@pytest_asyncio.fixture(params=BACKENDS)
async def svc(
    request: pytest.FixtureRequest, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[MemoryServiceHandle]:
    monkeypatch.setenv("SERVICE_JWT_SECRET", SECRET)
    monkeypatch.delenv("MEMORY_SERVICE_JWT_SECRET", raising=False)
    monkeypatch.delenv("MEMORY_SERVICE_TOKEN", raising=False)
    async with _database(request.param, tmp_path, "memory") as url, memory_service(url) as handle:
        yield handle


def _raw(svc: MemoryServiceHandle, tenant: str) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=svc.app),
        base_url="http://memory.test",
        headers={"Authorization": f"Bearer {memory_service_credential(tenant)}"},
    )


def _statement(text: str) -> dict[str, object]:
    return observation_out(
        ObservationDraft(
            observation_type=ObservationType.PROJECT,
            topic_hint="capture",
            statement=text,
            keywords=("durable",),
            aliases=(),
            extraction_model="model-test",
            prompt_version=PROMPT_VERSION,
            created_at=1_788_000_000,
            body="Bounded supporting detail.",
        )
    )


async def _notebook(client: NotebookClient, tenant: str = "t1") -> str:
    return (await client.ensure(tenant_id=tenant, owner="alice", agent_pin=PIN, name="default")).id


async def test_a_burst_queued_claimed_and_committed_through_the_client_lands_and_lifts_the_barrier(
    svc: MemoryServiceHandle,
) -> None:
    client = svc.client()
    nb = await _notebook(client)
    job = await client.capture_enqueue(tenant_id="t1", session_id="s", notebook_id=nb, run_ids=["r1", "r2"])
    assert (job.from_turn, job.through_turn) == (1, 2)
    assert await client.capture_enqueue(tenant_id="t1", session_id="s", notebook_id=nb, run_ids=["r1", "r2"]) == job

    assert await client.capture_tenants(limit=8) == ["t1"]
    lease = await client.capture_claim(tenant_id="t1", owner="w1", lease_s=60, job_id=job.job_id)
    assert lease is not None and lease.job == dataclasses.replace(job, attempt=1)
    outcome = {
        "observations": [_statement("The queue survives a worker's death")],
        "retire": [],
        "context": {"sensitivity": Sensitivity.ORDINARY.value, "inherited": []},
    }
    assert await client.capture_commit(lease, outcome) is False
    assert await client.capture_commit(lease, outcome) is True, "the same outcome again is a no-op"
    with pytest.raises(CaptureConflict):
        await client.capture_commit(lease, NOOP)

    barrier = await client.capture_flush(tenant_id="t1", session_id="s", timeout_s=30)
    assert barrier["result"] == "success", barrier
    assert barrier["cursors"] == {"requested": 2, "captured": 2, "indexed": 2}
    assert [f.text for f in await client.facts(nb)] == ["The queue survives a worker's death"]
    assert await client.capture_tenants(limit=8) == [], "nothing left to claim"


async def test_a_failure_is_held_and_reported_without_its_burst(svc: MemoryServiceHandle) -> None:
    client = svc.client()
    nb = await _notebook(client)
    await client.capture_enqueue(tenant_id="t1", session_id="s", notebook_id=nb, run_ids=["r1"])
    lease = await client.capture_claim(tenant_id="t1", owner="w1", lease_s=60)
    assert lease is not None
    await client.capture_fail(lease, "the model is down\nsecret=sk-abc", terminal=False)

    barrier = await client.capture_flush(tenant_id="t1", session_id="s", timeout_s=0)
    assert barrier["result"] == "retry"
    assert barrier["work"]["failed"] == 1 and barrier["cursors"]["captured"] == 0
    assert "\n" not in (barrier["work"]["last_error"] or ""), "diagnostics are sanitized"
    with pytest.raises(StaleLease):
        await client.capture_commit(lease, NOOP)  # given back: no longer this worker's
    assert await client.capture_claim(tenant_id="t1", owner="w2", lease_s=60) is None, "backing off"


async def test_another_tenant_cannot_queue_claim_commit_or_read_a_capture(svc: MemoryServiceHandle) -> None:
    client = svc.client()
    nb = await _notebook(client)
    job = await client.capture_enqueue(tenant_id="t1", session_id="s", notebook_id=nb, run_ids=["r1"])
    lease = await client.capture_claim(tenant_id="t1", owner="w1", lease_s=60)
    assert lease is not None
    wire_lease = {
        "job": {**{k: getattr(job, k) for k in ("job_id", "tenant_id", "session_id", "notebook_id")},
                "run_ids": list(job.run_ids), "from_turn": job.from_turn, "through_turn": job.through_turn,
                "attempt": lease.job.attempt},
        "owner": lease.owner,
        "expires_at": lease.expires_at,
    }
    async with _raw(svc, "t2") as other:
        attempts = {
            "enqueue as t1": other.post(
                "/v1/capture/jobs", json={"tenant_id": "t1", "session_id": "x", "notebook_id": nb, "run_ids": ["r"]}
            ),
            "enqueue into t1's notebook": other.post(
                "/v1/capture/jobs", json={"tenant_id": "t2", "session_id": "x", "notebook_id": nb, "run_ids": ["r"]}
            ),
            "claim": other.post("/v1/capture/jobs:claim", json={"tenant_id": "t1", "owner": "w", "lease_s": 60}),
            "commit": other.post(f"/v1/capture/jobs/{job.job_id}:commit", json={"lease": wire_lease, "outcome": NOOP}),
            "fail": other.post(f"/v1/capture/jobs/{job.job_id}:fail", json={"lease": wire_lease, "error": "x"}),
            "release": other.post(f"/v1/capture/jobs/{job.job_id}:release", json={"lease": wire_lease, "error": "x"}),
            "session": other.get("/v1/capture/sessions/s", params={"tenant_id": "t1"}),
            "flush": other.post("/v1/capture/sessions/s:flush", json={"tenant_id": "t1", "timeout_s": 0}),
            "notebook view": other.get(f"/v1/capture/notebooks/{nb}"),
        }
        codes = {name: (await call).status_code for name, call in attempts.items()}
        assert codes == {
            "enqueue as t1": 403,
            "enqueue into t1's notebook": 404,
            "claim": 403,
            "commit": 403,
            "fail": 403,
            "release": 403,
            "session": 403,
            "flush": 403,
            "notebook view": 404,
        }
        # As itself, t2 sees no session of t1's, and claims none of its jobs.
        assert (await other.get("/v1/capture/sessions/s", params={"tenant_id": "t2"})).status_code == 404
        claimed = await other.post("/v1/capture/jobs:claim", json={"tenant_id": "t2", "owner": "w", "lease_s": 60})
        assert claimed.json() == {"lease": None}
    with pytest.raises(UnknownCaptureSession) as unknown:
        await client.capture_flush(tenant_id="t2", session_id="s", timeout_s=0)
    assert unknown.value.session_id == "s"
    # t1's own lease is untouched by all of it.
    assert await client.capture_commit(lease, NOOP) is False


async def test_the_claimable_tenants_listing_names_tenants_only(svc: MemoryServiceHandle) -> None:
    client = svc.client()
    for tenant in ("t1", "t2"):
        nb = await _notebook(client, tenant)
        await client.capture_enqueue(tenant_id=tenant, session_id=f"s-{tenant}", notebook_id=nb, run_ids=["r1"])
    async with _raw(svc, "t3") as anyone:
        listed = await anyone.get("/v1/capture/tenants", params={"limit": 8})
    assert listed.status_code == 200 and listed.json() == {"tenants": ["t1", "t2"]}
    assert await client.capture_tenants(limit=8, pending_grace_s=600) == [], "pending jobs are their activity's first"


async def test_an_outcome_that_could_never_be_kept_is_refused_at_commit(svc: MemoryServiceHandle) -> None:
    client = svc.client()
    nb = await _notebook(client)
    await client.capture_enqueue(tenant_id="t1", session_id="s", notebook_id=nb, run_ids=["r1"])
    lease = await client.capture_claim(tenant_id="t1", owner="w1", lease_s=60)
    assert lease is not None
    broken = {**_statement("No body"), "body": ""}
    with pytest.raises(CaptureInvalid):
        await client.capture_commit(lease, {"observations": [broken], "retire": []})
    with pytest.raises(CaptureInvalid):
        await client.capture_commit(lease, {"observations": [], "retire": [], "context": "ordinary"})
    assert await client.capture_commit(lease, NOOP) is False, "refused, not committed: the lease still holds"


async def test_the_notebook_view_holds_the_statements_still_queued(svc: MemoryServiceHandle) -> None:
    memory = svc.app.state.memory
    await memory.stop_background()  # the inbox keeps what is queued until drained here
    memory.notebooks.on_queued = lambda: None
    client = svc.client()
    nb = await _notebook(client)
    before, queued = await client.capture_view(nb)
    assert queued == []
    await client.capture_enqueue(tenant_id="t1", session_id="s", notebook_id=nb, run_ids=["r1"])
    lease = await client.capture_claim(tenant_id="t1", owner="w1", lease_s=60)
    assert lease is not None
    outcome = {"observations": [_statement("Queued, not landed yet")], "retire": [],
               "context": {"sensitivity": Sensitivity.ORDINARY.value, "inherited": []}}
    await client.capture_commit(lease, outcome, resolved_at=before)
    after, queued = await client.capture_view(nb)
    assert after != before, "a queued statement changes the revision"
    ((text, provenance),) = queued
    assert text == "Queued, not landed yet"
    assert provenance.sensitivity is Sensitivity.ORDINARY, "with the provenance its fact will land with"
