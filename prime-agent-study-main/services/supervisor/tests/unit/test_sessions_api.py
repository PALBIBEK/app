"""Session API endpoint tests."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from supervisor.agent.events import ToolStart
from supervisor.api.sessions import router as sessions_router
from supervisor.app import build_supervisor
from supervisor.db.engine import open_db
from supervisor.main import create_app
from tests.support.session_env import make_catalog


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
        json={"text": "hi again", "command_id": "dup"},
        headers=HEADERS,
    )
    assert r1.status_code == 202
    assert r2.status_code == 202
    assert r1.json() == r2.json()
    # One echo turn → one RunEnd in journal (stateless cold may still one turn).
    import asyncio

    await asyncio.sleep(0.15)
    payloads = await state.manager.event_payloads(sid)
    assert payloads.count("RunEnd") == 1


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
async def test_send_recovery_blocked_409_names_tool(api) -> None:
    client, state, _ = api
    sid = (
        await client.post(
            "/v1/sessions",
            json={"harness_version": "kg-v1", "runtime": "echo"},
            headers=HEADERS,
        )
    ).json()["session_id"]
    await state.manager.terminate(sid)
    # Force cold session with unpaired non_atomic tool.
    from supervisor.journal.writer import JournalWriter

    # Re-open session row as active without runtime (cold).
    async with state.manager._db() as db:
        from supervisor.db.models import SessionRow

        row = await db.get(SessionRow, sid)
        assert row is not None
        row.status = "active"
        await db.commit()

    writer = JournalWriter(state.manager._db, blob_dir=None)
    await writer.append(
        sid,
        ToolStart(tool_call_id="tc-bash-1", tool_name="bash", args={"cmd": "rm"}),
        turn_id="t",
        trace_id=None,
    )

    r = await client.post(
        f"/v1/sessions/{sid}/messages",
        json={"text": "hi", "command_id": "after-crash"},
        headers=HEADERS,
    )
    assert r.status_code == 409
    detail = r.json()["detail"]
    assert "tc-bash-1" in str(detail)


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
    import asyncio

    await asyncio.sleep(0.1)
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
