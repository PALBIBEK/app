"""D4 §5 C–D / T8 T9 T11 — drain fail-closed + cost on detail."""

from __future__ import annotations

import asyncio
from uuid import uuid4

import pytest

from supervisor.agent.events import Usage
from tests.d4_acceptance.conftest import HEADERS_A, create_session, wait_for_journal


@pytest.mark.asyncio
async def test_t8_draining_create_returns_503(supervisor_stack) -> None:
    """T8 / §5 D: while draining, create returns 503."""
    client = supervisor_stack["client"]
    state = supervisor_stack["state"]
    state.draining = True

    r = await client.post(
        "/v1/sessions",
        headers=HEADERS_A,
        json={"harness_version": "kg-v1", "runtime": "echo"},
    )
    assert r.status_code == 503
    assert "drain" in r.text.lower() or r.json().get("detail") == "draining"


@pytest.mark.asyncio
async def test_readyz_503_draining(supervisor_stack) -> None:
    """§5 D: /readyz → 503 draining."""
    client = supervisor_stack["client"]
    state = supervisor_stack["state"]
    state.draining = True

    r = await client.get("/readyz")
    assert r.status_code == 503
    body = r.json()
    assert body.get("reason") == "draining"
    assert body.get("status") == "not_ready"


@pytest.mark.asyncio
async def test_t9_draining_message_returns_503(supervisor_stack) -> None:
    """T9 / §5 D: while draining, message returns 503 (fail-closed)."""
    client = supervisor_stack["client"]
    state = supervisor_stack["state"]

    created = await create_session(client)
    sid = created["session_id"]

    state.draining = True
    r = await client.post(
        f"/v1/sessions/{sid}/messages",
        headers=HEADERS_A,
        json={"text": "blocked", "command_id": f"drain-msg-{uuid4().hex}"},
    )
    assert r.status_code == 503


@pytest.mark.asyncio
async def test_draining_fork_returns_503(supervisor_stack) -> None:
    """§5 D: ideally fork also refuses while draining."""
    client = supervisor_stack["client"]
    state = supervisor_stack["state"]

    created = await create_session(client)
    sid = created["session_id"]
    await client.post(
        f"/v1/sessions/{sid}/messages",
        headers=HEADERS_A,
        json={"text": "seed", "command_id": f"drain-fork-{uuid4().hex}"},
    )

    state.draining = True
    r = await client.post(
        f"/v1/sessions/{sid}/fork",
        headers=HEADERS_A,
        json={},
    )
    assert r.status_code == 503


@pytest.mark.asyncio
async def test_t11_total_cost_exposed_on_detail_after_turn(supervisor_stack) -> None:
    """T11 / §5 C: usage cost persisted; detail exposes total_cost > 0."""
    client = supervisor_stack["client"]
    state = supervisor_stack["state"]

    created = await create_session(client)
    sid = created["session_id"]

    runtime = state.manager._registry.get(sid)
    assert runtime is not None
    runtime.forced_usage = Usage(
        input_tokens=10,
        output_tokens=5,
        cache_read=0,
        cache_write=0,
        cost_total=1.25,
    )

    msg = await client.post(
        f"/v1/sessions/{sid}/messages",
        headers=HEADERS_A,
        json={"text": "billable", "command_id": f"cost-{uuid4().hex}"},
    )
    assert msg.status_code == 202
    await wait_for_journal(state.reader, sid, min_events=3)

    # Budget/index update runs in fan-out after MessageEnd — poll detail.
    body: dict = {}
    for _ in range(50):
        detail = await client.get(f"/v1/sessions/{sid}", headers=HEADERS_A)
        assert detail.status_code == 200
        body = detail.json()
        if float(body.get("total_cost") or 0) > 0.0:
            break
        await asyncio.sleep(0.02)

    assert "total_cost" in body
    assert float(body["total_cost"]) > 0.0

    listed = await client.get("/v1/sessions", headers=HEADERS_A)
    assert listed.status_code == 200
    row = next(r for r in listed.json() if r["session_id"] == sid)
    assert "total_cost" in row
    assert float(row["total_cost"]) > 0.0


@pytest.mark.asyncio
async def test_status_enum_values_stable_on_wire(supervisor_stack) -> None:
    """§5 C: status enum stable for UI mapping."""
    allowed = {
        "active",
        "paused",
        "terminated",
        "failed",
        "awaiting_approval",
        "budget_exceeded",
    }
    client = supervisor_stack["client"]
    created = await create_session(client)
    sid = created["session_id"]
    detail = await client.get(f"/v1/sessions/{sid}", headers=HEADERS_A)
    assert detail.status_code == 200
    assert detail.json()["status"] in allowed

    await client.delete(f"/v1/sessions/{sid}", headers=HEADERS_A)
    after = await client.get(f"/v1/sessions/{sid}", headers=HEADERS_A)
    if after.status_code == 200:
        assert after.json()["status"] in allowed
