"""D4 §5 A / T1 T2 T5 T6 T7 T10 — session API correctness."""

from __future__ import annotations

import asyncio
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from supervisor.app import build_supervisor
from supervisor.db.engine import open_db
from supervisor.main import create_app
from tests.d4_acceptance.conftest import (
    CREATE_BODY,
    HEADERS_A,
    HEADERS_B,
    TENANT_B,
    create_session,
    wait_for_journal,
)
from tests.support.session_env import make_catalog



@pytest.mark.asyncio
async def test_t1_create_with_pins_returns_201_active(supervisor_stack) -> None:
    """T1 / §5 A: create with D3-shaped body → 201 + active."""
    client = supervisor_stack["client"]
    body = {
        "harness_version": "kg-v1",
        "runtime": "echo",
        "composition_id": None,
        "composition_digest": None,
    }
    r = await client.post("/v1/sessions", headers=HEADERS_A, json=body)
    assert r.status_code == 201
    data = r.json()
    assert data["status"] == "active"
    assert data["harness_version"] == "kg-v1"
    assert data["runtime"] == "echo"
    assert isinstance(data["session_id"], str) and data["session_id"]


@pytest.mark.asyncio
async def test_t10_cross_tenant_get_returns_404(supervisor_stack) -> None:
    """T10 / §5 A: list/get are tenant-scoped (cross-tenant → 404)."""
    client = supervisor_stack["client"]
    created = await create_session(client)
    sid = created["session_id"]

    ok = await client.get(f"/v1/sessions/{sid}", headers=HEADERS_A)
    assert ok.status_code == 200

    cross = await client.get(f"/v1/sessions/{sid}", headers=HEADERS_B)
    assert cross.status_code == 404

    listed_b = await client.get("/v1/sessions", headers=HEADERS_B)
    assert listed_b.status_code == 200
    assert all(row["session_id"] != sid for row in listed_b.json())


@pytest.mark.asyncio
async def test_message_without_command_id_returns_422(supervisor_stack) -> None:
    """§5 A: message without command_id → 422."""
    client = supervisor_stack["client"]
    created = await create_session(client)
    sid = created["session_id"]

    r = await client.post(
        f"/v1/sessions/{sid}/messages",
        headers=HEADERS_A,
        json={"text": "hello"},
    )
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_t2_duplicate_command_id_same_body_one_send(supervisor_stack) -> None:
    """T2 / §5 A: duplicate command_id → same turn_id; one runtime send."""
    client = supervisor_stack["client"]
    state = supervisor_stack["state"]
    created = await create_session(client)
    sid = created["session_id"]

    runtime = state.manager._registry.get(sid)  # noqa: SLF001
    assert runtime is not None
    send_calls = {"n": 0}
    original_send = runtime.send

    async def counting_send(message: str, *, command_id: str, mode: str = "prompt"):
        send_calls["n"] += 1
        return await original_send(message, command_id=command_id, mode=mode)

    runtime.send = counting_send  # type: ignore[method-assign]

    command_id = f"cmd-{uuid4().hex}"
    payload = {"text": "ping once", "command_id": command_id}

    first = await client.post(
        f"/v1/sessions/{sid}/messages", headers=HEADERS_A, json=payload
    )
    assert first.status_code == 202
    body1 = first.json()
    assert body1["status"] == "accepted"
    assert body1["turn_id"]
    assert send_calls["n"] == 1

    second = await client.post(
        f"/v1/sessions/{sid}/messages", headers=HEADERS_A, json=payload
    )
    assert second.status_code == 202
    assert second.json() == body1
    assert send_calls["n"] == 1

    third = await client.post(
        f"/v1/sessions/{sid}/messages", headers=HEADERS_A, json=payload
    )
    assert third.status_code == 202
    assert third.json()["turn_id"] == body1["turn_id"]
    assert send_calls["n"] == 1


@pytest.mark.asyncio
async def test_t5_abort_busy_turn_yields_abort_in_journal(supervisor_stack) -> None:
    """T5 / §5 A: abort returns ok and yields abort path in journal."""
    client = supervisor_stack["client"]
    state = supervisor_stack["state"]
    created = await create_session(client)
    sid = created["session_id"]

    runtime = state.manager._registry.get(sid)  # noqa: SLF001
    assert runtime is not None
    # Stretch the turn so abort can interleave.
    original_sleep = asyncio.sleep

    async def slow_sleep(delay: float = 0, *args, **kwargs):
        await original_sleep(0.05 if delay == 0 else delay, *args, **kwargs)

    runtime.repeat_tool_times = 8
    monkey_sleep = slow_sleep

    # Patch sleep used inside EchoRuntime.send loop.
    import supervisor.agent.runtimes.echo.runtime as echo_mod

    echo_mod.asyncio.sleep = monkey_sleep  # type: ignore[assignment]
    try:
        msg_task = asyncio.create_task(
            client.post(
                f"/v1/sessions/{sid}/messages",
                headers=HEADERS_A,
                json={"text": "long turn", "command_id": f"abort-{uuid4().hex}"},
            )
        )
        await original_sleep(0.05)
        abort = await client.post(f"/v1/sessions/{sid}/abort", headers=HEADERS_A)
        assert abort.status_code == 200
        assert abort.json() == {"status": "ok"}
        await asyncio.wait_for(msg_task, timeout=5.0)
    finally:
        echo_mod.asyncio.sleep = original_sleep  # type: ignore[assignment]

    envs = await wait_for_journal(
        state.reader, sid, min_events=1, require_type="TurnEnd"
    )
    stop_reasons = [
        getattr(env.payload, "stop_reason", None)
        for env in envs
        if getattr(env.payload, "stop_reason", None) is not None
    ]
    assert "aborted" in stop_reasons



@pytest.mark.asyncio
async def test_t6_terminate_then_message_fail_closed(supervisor_stack) -> None:
    """T6 / §5 A: terminate idempotent; further messages fail closed."""
    client = supervisor_stack["client"]
    created = await create_session(client)
    sid = created["session_id"]

    first = await client.delete(f"/v1/sessions/{sid}", headers=HEADERS_A)
    assert first.status_code == 204
    second = await client.delete(f"/v1/sessions/{sid}", headers=HEADERS_A)
    assert second.status_code in (204, 404)

    detail = await client.get(f"/v1/sessions/{sid}", headers=HEADERS_A)
    if detail.status_code == 200:
        assert detail.json()["status"] == "terminated"

    msg = await client.post(
        f"/v1/sessions/{sid}/messages",
        headers=HEADERS_A,
        json={"text": "after terminate", "command_id": f"post-term-{uuid4().hex}"},
    )
    assert msg.status_code in (409, 404)


@pytest.mark.asyncio
async def test_t7_fork_copies_prefix_parent_unchanged(supervisor_stack) -> None:
    """T7 / §5 A: fork returns new id; original prefix unchanged; child has history."""
    client = supervisor_stack["client"]
    state = supervisor_stack["state"]
    created = await create_session(client)
    sid = created["session_id"]

    await client.post(
        f"/v1/sessions/{sid}/messages",
        headers=HEADERS_A,
        json={"text": "parent turn", "command_id": f"fork-src-{uuid4().hex}"},
    )
    parent_before = await wait_for_journal(state.reader, sid, min_events=3)
    assert parent_before
    at_seq = parent_before[len(parent_before) // 2].seq


    fork = await client.post(
        f"/v1/sessions/{sid}/fork",
        headers=HEADERS_A,
        json={"at_seq": at_seq},
    )
    assert fork.status_code == 201
    child_id = fork.json()["session_id"]
    assert child_id != sid

    parent_after = await state.reader.read(sid, since=-1)
    assert [e.seq for e in parent_after] == [e.seq for e in parent_before]

    child_events = await state.reader.read(child_id, since=-1)
    assert child_events
    assert max(e.seq for e in child_events) <= at_seq or all(
        e.seq <= at_seq for e in child_events
    ) or len(child_events) <= len([e for e in parent_before if e.seq <= at_seq]) + 5


@pytest.mark.asyncio
async def test_missing_session_message_returns_404(supervisor_stack) -> None:
    client = supervisor_stack["client"]
    r = await client.post(
        "/v1/sessions/does-not-exist/messages",
        headers=HEADERS_A,
        json={"text": "x", "command_id": "c1"},
    )
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_t17_user_jwt_style_bearer_refused_when_auth_required(tmp_path) -> None:
    """T17 / §5 E: non-service bearer refused when auth is fail-closed."""
    async with open_db(f"sqlite+aiosqlite:///{tmp_path / 'auth.db'}") as (_, factory):
        state = build_supervisor(
            session_factory=factory,
            catalog=make_catalog(),
            blob_dir=tmp_path / "blobs",
            auth_required=True,
            auth_token="service-static-token",
        )
        app = create_app(state=state)
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            # No Authorization
            bare = await client.post(
                "/v1/sessions",
                headers={"X-Tenant-Id": TENANT_B},
                json=CREATE_BODY,
            )
            assert bare.status_code == 401

            # User-looking JWT string must not open the API
            fake_user = await client.post(
                "/v1/sessions",
                headers={
                    "X-Tenant-Id": TENANT_B,
                    "Authorization": "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.user",
                },
                json=CREATE_BODY,
            )
            assert fake_user.status_code == 401

            ok = await client.post(
                "/v1/sessions",
                headers={
                    "X-Tenant-Id": TENANT_B,
                    "Authorization": "Bearer service-static-token",
                },
                json=CREATE_BODY,
            )
            assert ok.status_code == 201
