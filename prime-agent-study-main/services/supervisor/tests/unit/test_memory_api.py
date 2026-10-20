"""Sessions carry their memory binding; the memory API acts as the calling principal."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from supervisor.app import SupervisorState, build_supervisor
from supervisor.db.engine import open_db
from supervisor.db.models import SessionRow
from supervisor.main import create_app
from supervisor.memory.binding import MemoryBinding, MemoryRequest, agent_pin
from tests.support.session_env import make_catalog

# Notebooks live in the memory service: a live one, found as production finds it.
pytestmark = [pytest.mark.asyncio, pytest.mark.usefixtures("memory_service_url")]

ALICE = {"X-Tenant-Id": "t1", "X-Principal-Id": "alice"}
BOB = {"X-Tenant-Id": "t1", "X-Principal-Id": "bob"}


@pytest_asyncio.fixture
async def api(tmp_path: Path) -> AsyncIterator[tuple[AsyncClient, SupervisorState]]:
    async with open_db(f"sqlite+aiosqlite:///{tmp_path / 'api.db'}") as (_, factory):
        state = build_supervisor(
            session_factory=factory, catalog=make_catalog(), blob_dir=tmp_path / "blobs"
        )

        async def unknown(system: str, content: str) -> str:
            return "ordinary"

        state.memory._one_shot = unknown  # type: ignore[union-attr]
        transport = ASGITransport(app=create_app(state=state))
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            yield client, state


async def _binding(state: SupervisorState, sid: str) -> MemoryBinding:
    async with state.manager._db() as db:
        row = await db.get(SessionRow, sid)
        assert row is not None and row.memory
        assert (row.owner_principal_id, row.agent_pin) == (
            row.memory["owner"],
            row.memory["agent_pin"],
        )
        return MemoryBinding.from_dict(row.memory)


def test_a_pin_names_the_exact_version() -> None:
    snapshot = {"harness_version": "kg-v1", "runtime": "echo"}
    pin = agent_pin(harness_version="kg-v1", manifest_snapshot=snapshot, composition_pins=None)
    edited = agent_pin(
        harness_version="kg-v1",
        manifest_snapshot={**snapshot, "runtime": "x"},
        composition_pins=None,
    )
    assert pin.startswith("harness:kg-v1@sha256:") and pin != edited, "same version, new content"
    composed = agent_pin(
        harness_version="githi-v1",
        manifest_snapshot=snapshot,
        composition_pins={"composition_id": "mine", "composition_digest": "sha256:abc"},
    )
    assert composed == "composition:mine@sha256:abc"


def test_memory_requests_are_validated() -> None:
    with pytest.raises(ValueError):
        MemoryRequest(read=("",))
    with pytest.raises(ValueError):
        MemoryRequest(capture_max_turns=0)
    assert MemoryRequest.from_dict({"read": ["a", "b"], "recall": "writable"}).read == ("a", "b")


async def test_a_session_binds_its_owners_notebooks(
    api: tuple[AsyncClient, SupervisorState],
) -> None:
    client, state = api
    created = await client.post(
        "/v1/sessions",
        json={
            "harness_version": "kg-v1",
            "memory": {"write": "work", "read": ["work", "personal"]},
        },
        headers=ALICE,
    )
    assert created.status_code == 201, created.text
    sid = created.json()["session_id"]
    binding = await _binding(state, sid)
    assert binding.owner == "alice"
    assert len(binding.read) == 2 and binding.write == binding.read[0]
    assert set(binding.labels.values()) == {"work", "personal"}
    listed = await client.get(
        "/v1/memory/notebooks", params={"agent_pin": binding.agent_pin}, headers=ALICE
    )
    assert sorted(n["name"] for n in listed.json()) == ["personal", "work"]
    # The same names for another session of the same agent reuse the same notebooks.
    again = await client.post(
        "/v1/sessions",
        json={"harness_version": "kg-v1", "memory": {"write": "work", "read": ["work"]}},
        headers=ALICE,
    )
    assert (await _binding(state, again.json()["session_id"])).write == binding.write
    # Bob's session of the same agent gets Bob's own notebooks.
    bobs = await client.post("/v1/sessions", json={"harness_version": "kg-v1"}, headers=BOB)
    bob_binding = await _binding(state, bobs.json()["session_id"])
    assert bob_binding.owner == "bob" and bob_binding.write not in binding.read
    # A fork keeps the owner, the pin and the notebooks.
    forked = await client.post(f"/v1/sessions/{sid}/fork", headers=ALICE)
    assert await _binding(state, forked.json()["session_id"]) == binding


async def test_facts_round_trip_through_the_api(api: tuple[AsyncClient, SupervisorState]) -> None:
    client, state = api
    sid = (
        await client.post("/v1/sessions", json={"harness_version": "kg-v1"}, headers=ALICE)
    ).json()["session_id"]
    notebook = (await _binding(state, sid)).write
    base = f"/v1/memory/notebooks/{notebook}"
    added = await client.post(
        f"{base}/facts",
        json={"facts": ["Prefers tea", "prefers TEA", "Owns billing"]},
        headers=ALICE,
    )
    assert added.json() == {"added": 2}
    facts = (await client.get(f"{base}/facts", headers=ALICE)).json()
    assert [f["text"] for f in facts] == ["Prefers tea", "Owns billing"]
    assert {f["sensitivity"] for f in facts} == {"ordinary"} and not facts[0]["source_unknown"]
    tea, billing = facts
    assert (
        await client.patch(
            f"{base}/facts",
            json={"edits": [{"id": tea["id"], "text": "Prefers green tea"}]},
            headers=ALICE,
        )
    ).status_code == 204
    missing = await client.patch(
        f"{base}/facts", json={"edits": [{"id": "999", "text": "x"}]}, headers=ALICE
    )
    assert missing.status_code == 404 and missing.json()["detail"]["ids"] == ["999"]
    assert (
        await client.request(
            "DELETE", f"{base}/facts", json={"ids": [billing["id"]]}, headers=ALICE
        )
    ).status_code == 204
    hits = await client.post(
        "/v1/memory/search",
        json={"agent_pin": (await _binding(state, sid)).agent_pin, "query": "green"},
        headers=ALICE,
    )
    assert [h["fact"].split(") ", 1)[1] for h in hits.json()] == ["Prefers green tea"]
    revisions = (await client.get(f"{base}/revisions", headers=ALICE)).json()
    assert [r["op"] for r in revisions] == ["insert", "insert", "update", "delete"]
    before_edit = revisions[0]["revision"]
    old = (
        await client.get(f"{base}/facts", params={"at_revision": before_edit}, headers=ALICE)
    ).json()
    assert [f["text"] for f in old] == ["Prefers tea", "Owns billing"]
    assert (
        await client.post(f"{base}/restore", json={"revision": before_edit}, headers=ALICE)
    ).status_code == 204
    assert [f["text"] for f in (await client.get(f"{base}/facts", headers=ALICE)).json()] == [
        "Prefers tea",
        "Owns billing",
    ]
    assert (await client.post(f"{base}/clear", headers=ALICE)).json() == {"removed": 2}
    assert (await client.get(f"{base}/facts", headers=ALICE)).json() == []


async def test_someone_elses_notebook_does_not_exist(
    api: tuple[AsyncClient, SupervisorState],
) -> None:
    client, state = api
    sid = (
        await client.post("/v1/sessions", json={"harness_version": "kg-v1"}, headers=ALICE)
    ).json()["session_id"]
    binding = await _binding(state, sid)
    base = f"/v1/memory/notebooks/{binding.write}"
    for method, path, body in (
        ("GET", f"{base}/facts", None),
        ("POST", f"{base}/facts", {"facts": ["x"]}),
        ("POST", f"{base}/clear", None),
        ("GET", f"{base}/revisions", None),
        ("PATCH", base, {"archived": True}),
    ):
        resp = await client.request(method, path, json=body, headers=BOB)
        assert resp.status_code == 404, (method, path, resp.text)
    search = await client.post(
        "/v1/memory/search", json={"agent_pin": binding.agent_pin, "query": "x"}, headers=BOB
    )
    assert search.json() == []


async def test_notebooks_are_named_once_and_can_be_archived(
    api: tuple[AsyncClient, SupervisorState],
) -> None:
    client, _ = api
    body = {"agent_pin": "harness:kg-v1@sha256:x", "name": "project", "label": "Project X"}
    made = await client.post("/v1/memory/notebooks", json=body, headers=ALICE)
    assert made.status_code == 201 and made.json()["label"] == "Project X"
    assert (await client.post("/v1/memory/notebooks", json=body, headers=ALICE)).status_code == 409
    archived = await client.patch(
        f"/v1/memory/notebooks/{made.json()['id']}", json={"archived": True}, headers=ALICE
    )
    assert archived.json()["archived"] is True
    params = {"agent_pin": body["agent_pin"]}
    assert (await client.get("/v1/memory/notebooks", params=params, headers=ALICE)).json() == []
    every = await client.get(
        "/v1/memory/notebooks", params={**params, "archived": True}, headers=ALICE
    )
    assert [n["name"] for n in every.json()] == ["project"]
