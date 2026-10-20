"""Session API endpoint tests."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from supervisor.api.sessions import router as sessions_router
from supervisor.app import build_supervisor
from supervisor.db.engine import open_db
from supervisor.main import create_app
from tests.support.session_env import make_catalog
from tests.support.waits import payload_names

HEADERS = {"X-Tenant-Id": "t1"}


@pytest.fixture
async def api(tmp_path):
    async with open_db(f"sqlite+aiosqlite:///{tmp_path / 'api.db'}") as (_, factory):
        state = build_supervisor(
            session_factory=factory,
            catalog=make_catalog(),
            blob_dir=tmp_path / "blobs",
            live_queue_max=32,
        )
        app = create_app(state=state)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            yield client, state, factory


@pytest.mark.asyncio
async def test_create_returns_201_and_id(api) -> None:
    client, _, _ = api
    r = await client.post(
        "/v1/sessions",
        json={"harness_version": "kg-v1", "runtime": "echo"},
        headers=HEADERS,
    )
    assert r.status_code == 201
    assert r.json()["session_id"]


@pytest.mark.asyncio
async def test_send_returns_202_and_turn_id(api) -> None:
    client, _, _ = api
    sid = (
        await client.post(
            "/v1/sessions",
            json={"harness_version": "kg-v1", "runtime": "echo"},
            headers=HEADERS,
        )
    ).json()["session_id"]
    r = await client.post(
        f"/v1/sessions/{sid}/messages",
        json={"text": "hi", "command_id": "c1"},
        headers=HEADERS,
    )
    assert r.status_code == 202
    assert r.json()["turn_id"]
    assert r.json()["status"] == "accepted"


@pytest.mark.asyncio
async def test_duplicate_command_id_returns_stored_and_sends_once(api) -> None:
    client, state, _ = api
    sid = (
        await client.post(
            "/v1/sessions",
            json={"harness_version": "kg-v1", "runtime": "echo"},
            headers=HEADERS,
        )
    ).json()["session_id"]
    r1 = await client.post(
        f"/v1/sessions/{sid}/messages",
        json={"text": "hi", "command_id": "dup"},
        headers=HEADERS,
    )
    r2 = await client.post(
        f"/v1/sessions/{sid}/messages",
        json={"text": "hi", "command_id": "dup"},
        headers=HEADERS,
    )
    assert r1.status_code == 202
    assert r2.status_code == 202
    assert r1.json() == r2.json()
    # The same id with another message is a conflict, not a replay of the first (F33).
    r3 = await client.post(
        f"/v1/sessions/{sid}/messages",
        json={"text": "hi again", "command_id": "dup"},
        headers=HEADERS,
    )
    assert r3.status_code == 409
    # Barrier, once the first turn is over (in-process sessions run one turn at a time):
    # a new message's turn comes after any second turn the duplicates could have started.
    await payload_names(state.manager, sid, lambda names: "RunEnd" in names)
    r4 = await client.post(
        f"/v1/sessions/{sid}/messages",
        json={"text": "barrier", "command_id": "after-conflict"},
        headers=HEADERS,
    )
    assert r4.status_code == 202
    payloads = await payload_names(state.manager, sid, lambda n: n.count("RunEnd") >= 2)
    assert payloads.count("RunEnd") == 2, "the duplicate and the conflict never ran"



@pytest.mark.asyncio
async def test_missing_command_id_rejected(api) -> None:
    client, _, _ = api
    sid = (
        await client.post(
            "/v1/sessions",
            json={"harness_version": "kg-v1", "runtime": "echo"},
            headers=HEADERS,
        )
    ).json()["session_id"]
    r = await client.post(
        f"/v1/sessions/{sid}/messages",
        json={"text": "hi"},
        headers=HEADERS,
    )
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_send_unknown_session_404(api) -> None:
    client, _, _ = api
    r = await client.post(
        "/v1/sessions/deadbeefdeadbeefdeadbeefdeadbeef/messages",
        json={"text": "hi", "command_id": "c1"},
        headers=HEADERS,
    )
    assert r.status_code == 404




@pytest.mark.asyncio
async def test_fork_returns_new_id_original_unchanged(api) -> None:
    client, state, _ = api
    sid = (
        await client.post(
            "/v1/sessions",
            json={"harness_version": "kg-v1", "runtime": "echo"},
            headers=HEADERS,
        )
    ).json()["session_id"]
    await client.post(
        f"/v1/sessions/{sid}/messages",
        json={"text": "hi", "command_id": "c1"},
        headers=HEADERS,
    )
    await payload_names(state.manager, sid, lambda names: "RunEnd" in names)
    before = await state.manager.snapshot_events(sid)
    r = await client.post(f"/v1/sessions/{sid}/fork", headers=HEADERS)
    assert r.status_code == 201
    new_id = r.json()["session_id"]
    assert new_id != sid
    after = await state.manager.snapshot_events(sid)
    assert before == after


@pytest.mark.asyncio
async def test_terminate_idempotent(api) -> None:
    client, _, _ = api
    sid = (
        await client.post(
            "/v1/sessions",
            json={"harness_version": "kg-v1", "runtime": "echo"},
            headers=HEADERS,
        )
    ).json()["session_id"]
    r1 = await client.delete(f"/v1/sessions/{sid}", headers=HEADERS)
    r2 = await client.delete(f"/v1/sessions/{sid}", headers=HEADERS)
    assert r1.status_code == 204
    assert r2.status_code == 204


@pytest.mark.asyncio
async def test_draining_blocks_message_returns_503(api) -> None:
    client, state, _ = api
    sid = (
        await client.post(
            "/v1/sessions",
            json={"harness_version": "kg-v1", "runtime": "echo"},
            headers=HEADERS,
        )
    ).json()["session_id"]
    state.draining = True
    r = await client.post(
        f"/v1/sessions/{sid}/messages",
        json={"text": "ping", "command_id": "cmd-drain-1"},
        headers=HEADERS,
    )
    assert r.status_code == 503
    assert r.json()["detail"] == "draining"


@pytest.mark.asyncio
async def test_draining_blocks_fork_returns_503(api) -> None:
    client, state, _ = api
    sid = (
        await client.post(
            "/v1/sessions",
            json={"harness_version": "kg-v1", "runtime": "echo"},
            headers=HEADERS,
        )
    ).json()["session_id"]
    state.draining = True
    r = await client.post(f"/v1/sessions/{sid}/fork", json={}, headers=HEADERS)
    assert r.status_code == 503
    assert r.json()["detail"] == "draining"


@pytest.mark.asyncio
async def test_session_detail_exposes_total_cost(api) -> None:
    client, _, _ = api
    sid = (
        await client.post(
            "/v1/sessions",
            json={"harness_version": "kg-v1", "runtime": "echo"},
            headers=HEADERS,
        )
    ).json()["session_id"]
    r = await client.get(f"/v1/sessions/{sid}", headers=HEADERS)
    assert r.status_code == 200
    body = r.json()
    assert "total_cost" in body
    assert body["total_cost"] == 0.0


def test_handlers_import_nothing_from_journal_or_runtimes() -> None:
    path = Path(sessions_router.__file__) if False else (
        Path(__file__).resolve().parents[2]
        / "src"
        / "supervisor"
        / "api"
        / "sessions.py"
    )
    tree = ast.parse(path.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            assert "journal" not in node.module, node.module
            assert "runtimes" not in node.module, node.module
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert "journal" not in alias.name
                assert "runtimes" not in alias.name
