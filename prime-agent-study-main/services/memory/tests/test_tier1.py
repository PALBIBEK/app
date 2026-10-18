"""Comprehensive Phase 1 tests — red→green against Memory/memory.md exit criteria."""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from memory_service.app import create_app
from memory_service.service import MemoryService
from memory_service.types import (
    FORMAT_VERSION,
    FormatUnsupported,
    Scope,
    SessionCorrupt,
    StaleFence,
)


@pytest.fixture
async def svc():
    s = MemoryService(database_url="sqlite+aiosqlite:///:memory:")
    await s.startup()
    yield s
    await s.aclose()


def scope() -> Scope:
    return Scope(tenant="t1", subject="repo:yal", namespace="facts")


# --- DM-U1 / DM-U2 ---


@pytest.mark.asyncio
async def test_create_session_persists_scope_header_not_event_zero(svc: MemoryService):
    handle = await svc.create_session(scope())
    logical = await svc.load(handle.id)
    assert logical.handle.scope.tenant == "t1"
    assert logical.handle.format_version == FORMAT_VERSION
    assert logical.events == ()
    assert logical.created_at > 0


@pytest.mark.asyncio
async def test_scope_required():
    with pytest.raises(ValueError, match="SCOPE_REQUIRED"):
        Scope(tenant="", subject="s", namespace="n")
    with pytest.raises(ValueError, match="SCOPE_REQUIRED"):
        Scope(tenant="t", subject="", namespace="n")


# --- DM-U3 / DM-U4 / DM-U6 ---


@pytest.mark.asyncio
async def test_buffer_append_sync_flush_persists_contiguous(svc: MemoryService):
    handle = await svc.create_session(scope())
    svc.append(handle.id, "user/message", {"text": "hi"})
    assert svc.pending_count(handle.id) == 1
    # Not durable yet
    mid = await svc.load(handle.id)
    assert mid.events == ()
    await svc.flush(handle.id)
    assert svc.pending_count(handle.id) == 0
    loaded = await svc.load(handle.id)
    assert len(loaded.events) == 1
    assert loaded.events[0].seq == 0
    assert loaded.events[0].type == "user/message"


@pytest.mark.asyncio
async def test_no_timer_flush_api(svc: MemoryService):
    assert not hasattr(svc, "start_timer_flush")
    assert not hasattr(svc, "flush_interval")


@pytest.mark.asyncio
async def test_seq_equals_index(svc: MemoryService):
    handle = await svc.create_session(scope())
    for i in range(5):
        svc.append(handle.id, "user/message", {"i": i})
    await svc.flush(handle.id)
    loaded = await svc.load(handle.id)
    for i, ev in enumerate(loaded.events):
        assert ev.seq == i


# --- DM-U7 / DM-U8 / DM-U11 ---


@pytest.mark.asyncio
async def test_load_repairs_open_tool_call_never_truncates(svc: MemoryService):
    handle = await svc.create_session(scope())
    svc.append(handle.id, "user/message", {"text": "do it"})
    svc.append(handle.id, "assistant/message", {"text": "calling"})
    svc.append(handle.id, "step/start", {})
    svc.append(
        handle.id,
        "tool/call",
        {"call_id": "c1", "name": "charge_card", "arguments": {"n": 1}},
    )
    await svc.flush(handle.id)

    logical = await svc.load(handle.id)
    assert logical.repaired is True
    types = [e.type for e in logical.events]
    assert types[:4] == ["user/message", "assistant/message", "step/start", "tool/call"]
    assert "tool/result" in types
    result = next(e for e in logical.events if e.type == "tool/result")
    assert result.data["status"] == "interrupted"
    assert result.data["recovered"] is True
    assert types[-1] == "turn/end"
    # prefix preserved
    assert logical.events[0].data["text"] == "do it"

    # Idempotent
    again = await svc.load(handle.id)
    results = [e for e in again.events if e.type == "tool/result"]
    assert len(results) == 1


# --- DM-U9 / DM-U10 ---


@pytest.mark.asyncio
async def test_seq_gap_marks_corrupt(svc: MemoryService):
    handle = await svc.create_session(scope())
    svc.append(handle.id, "user/message", {"text": "a"})
    await svc.flush(handle.id)
    await svc.inject_corrupt_gap_for_test(handle.id)
    with pytest.raises(SessionCorrupt):
        await svc.load(handle.id)


@pytest.mark.asyncio
async def test_unknown_format_version_rejected(svc: MemoryService):
    handle = await svc.create_session(scope())
    from memory_service.models import MemorySessionRow

    async with svc._factory() as db:  # noqa: SLF001
        row = await db.get(MemorySessionRow, handle.id)
        assert row is not None
        row.format_version = 999
        await db.commit()
    with pytest.raises(FormatUnsupported):
        await svc.load(handle.id)


# --- DM-U12 / DM-U13 ---


@pytest.mark.asyncio
async def test_outbox_exactly_once(svc: MemoryService):
    handle = await svc.create_session(scope())
    svc.append(handle.id, "assistant/message", {"text": "charge"})
    svc.append(
        handle.id,
        "tool/call",
        {
            "call_id": "pay-1",
            "name": "charge_card",
            "arguments": {"amount": 10},
            "side_effect": True,
            "idempotency_key": "idem-pay-1",
        },
    )
    await svc.flush(handle.id)

    calls: list[dict] = []

    def executor(payload: dict) -> dict:
        calls.append(payload)
        return {"charged": True, "amount": payload["arguments"]["amount"]}

    await svc.execute_outbox(handle.id, executor)
    assert len(calls) == 1

    # Crash simulation: execute again — should not re-run effect
    await svc.execute_outbox(handle.id, executor)
    assert len(calls) == 1

    loaded = await svc.load(handle.id)
    results = [e for e in loaded.events if e.type == "tool/result"]
    assert len(results) == 1
    assert results[0].data["result"]["charged"] is True


# --- DM-U14 / DM-U15 ---


@pytest.mark.asyncio
async def test_lease_fence_rejects_stale_writer(svc: MemoryService):
    handle = await svc.create_session(scope())
    fence_a = await svc.acquire_lease(handle.id, "pod-a")
    svc.append(handle.id, "user/message", {"text": "a"})
    await svc.flush(handle.id, fence=fence_a)

    fence_b = await svc.acquire_lease(handle.id, "pod-a")  # renew bumps fence
    assert fence_b != fence_a
    svc.append(handle.id, "user/message", {"text": "stale"})
    with pytest.raises(StaleFence):
        await svc.flush(handle.id, fence=fence_a)


@pytest.mark.asyncio
async def test_two_pods_race_lease(svc: MemoryService):
    from memory_service.types import LeaseHeld

    handle = await svc.create_session(scope())
    f1 = await svc.acquire_lease(handle.id, "pod-a")
    assert f1 >= 1
    with pytest.raises(LeaseHeld):
        await svc.acquire_lease(handle.id, "pod-b")


# --- DM-U16 / DM-U17 ---


@pytest.mark.asyncio
async def test_fork_and_list(svc: MemoryService):
    handle = await svc.create_session(scope())
    svc.append(handle.id, "user/message", {"text": "1"})
    svc.append(handle.id, "user/message", {"text": "2"})
    await svc.flush(handle.id)
    child = await svc.fork(handle.id, at_seq=0)
    child_loaded = await svc.load(child.id)
    assert len(child_loaded.events) == 1
    assert child_loaded.parent_id == handle.id
    assert child_loaded.forked_at_seq == 0

    listed = await svc.list_sessions(scope())
    ids = {s.id for s in listed}
    assert handle.id in ids and child.id in ids


# --- DM-U18 latency ---


@pytest.mark.asyncio
async def test_buffered_append_latency_budget(svc: MemoryService):
    handle = await svc.create_session(scope())
    t0 = time.perf_counter()
    for i in range(1000):
        svc.append(handle.id, "user/message", {"i": i})
    elapsed_ms = (time.perf_counter() - t0) * 1000
    # CI-loose: 1000 sync appends well under 500ms (p99 target is per-append <5ms)
    assert elapsed_ms < 500, f"append batch too slow: {elapsed_ms:.1f}ms"


# --- DM-U19 ordering ---


@pytest.mark.asyncio
async def test_assistant_before_tool_ordering_guard(svc: MemoryService):
    handle = await svc.create_session(scope())
    svc.append(handle.id, "tool/call", {"call_id": "x", "name": "t"})
    with pytest.raises(AssertionError, match="assistant/message"):
        svc.assert_assistant_before_tools(handle.id)
    # fix order
    handle2 = await svc.create_session(Scope(tenant="t1", subject="r", namespace="n"))
    svc.append(handle2.id, "assistant/message", {"text": "ok"})
    svc.append(handle2.id, "tool/call", {"call_id": "y", "name": "t"})
    svc.assert_assistant_before_tools(handle2.id)


# --- DM-C2 ---


def test_no_supervisor_imports():
    root = Path(__file__).resolve().parents[1] / "src"
    offenders: list[str] = []
    for path in root.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if "import supervisor" in text or "from supervisor" in text:
            offenders.append(str(path))
    assert offenders == []


# --- DM-C1 ---


def test_contract_exists():
    # tests/ -> memory/ -> services/ -> agent-platform/contracts
    contract = Path(__file__).resolve().parents[2].parent / "contracts" / "durable_memory.md"
    assert contract.is_file(), contract


# --- DM-I1 / DM-I2 ---


@pytest.mark.asyncio
async def test_http_round_trip_and_repair():
    app = create_app(database_url="sqlite+aiosqlite:///:memory:")
    with TestClient(app) as client:
        r = client.get("/v1/healthz")
        assert r.status_code == 200
        r = client.post(
            "/v1/sessions",
            json={
                "scope": {"tenant": "t", "subject": "s", "namespace": "n"},
                "session_id": "sess-http-1",
            },
        )
        assert r.status_code == 201, r.text
        sid = r.json()["id"]
        r = client.post(
            f"/v1/sessions/{sid}/events:buffer",
            json={
                "events": [
                    {"type": "assistant/message", "data": {"text": "x"}},
                    {
                        "type": "tool/call",
                        "data": {"call_id": "c", "name": "t", "side_effect": False},
                    },
                ]
            },
        )
        assert r.status_code == 200
        r = client.post(f"/v1/sessions/{sid}/flush", json={})
        assert r.status_code == 200
        r = client.get(f"/v1/sessions/{sid}")
        assert r.status_code == 200
        body = r.json()
        assert body["repaired"] is True
        assert body["events"][-1]["type"] == "turn/end"


# --- DM-X1 chaos sim ---


@pytest.mark.asyncio
async def test_chaos_unflushed_lost_flushed_durable_mid_outbox(svc: MemoryService):
    handle = await svc.create_session(scope())
    svc.append(handle.id, "user/message", {"text": "unflushed"})
    # Crash before flush — drop process-local buffer (kill -9 loses unflushed)
    svc._buffers.pop(handle.id, None)  # noqa: SLF001
    loaded = await svc.load(handle.id)
    assert loaded.events == ()

    svc.append(handle.id, "user/message", {"text": "durable"})
    await svc.flush(handle.id)
    loaded = await svc.load(handle.id)
    assert len(loaded.events) == 1
    assert loaded.events[0].data["text"] == "durable"

    # Mid-outbox: pending then execute twice
    svc.append(
        handle.id,
        "tool/call",
        {
            "call_id": "c2",
            "name": "side",
            "arguments": {},
            "side_effect": True,
            "idempotency_key": "k2",
        },
    )
    await svc.flush(handle.id)
    n = {"count": 0}

    def ex(p: dict) -> dict:
        n["count"] += 1
        return {"ok": True}

    await svc.execute_outbox(handle.id, ex)
    await svc.execute_outbox(handle.id, ex)
    assert n["count"] == 1
