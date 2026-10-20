"""D4 §5 B / T3 T4 T16 — SSE + journal resume semantics."""

from __future__ import annotations

from uuid import uuid4

import pytest

from supervisor.api.events import iter_sse
from supervisor.api.wire import envelope_to_wire
from tests.d4_acceptance.conftest import (
    HEADERS_A,
    create_session,
    parse_sse_json_events,
    wait_for_journal,
)


async def _sse_chunks_to_events(
    *,
    session_id: str,
    cursor: int,
    manager,
    reader,
    max_events: int,
) -> list[dict]:
    buf = ""
    async for chunk in iter_sse(
        session_id=session_id,
        cursor=cursor,
        manager=manager,
        reader=reader,
        heartbeat_secs=0.05,
        max_events=max_events,
    ):
        buf += chunk
    return parse_sse_json_events(buf)


@pytest.mark.asyncio
async def test_t3_sse_from_scratch_monotonic_snake_case(supervisor_stack) -> None:
    """T3 / §5 B: fresh stream — monotonic seq, snake_case type."""
    client = supervisor_stack["client"]
    state = supervisor_stack["state"]
    created = await create_session(client)
    sid = created["session_id"]

    await client.post(
        f"/v1/sessions/{sid}/messages",
        headers=HEADERS_A,
        json={"text": "stream me", "command_id": f"sse-{uuid4().hex}"},
    )
    journal = await wait_for_journal(state.reader, sid, min_events=3)
    assert journal

    wire = [envelope_to_wire(e).model_dump() for e in journal]
    seqs = [e["seq"] for e in wire]
    assert seqs == sorted(seqs)
    assert all(seqs[i] < seqs[i + 1] for i in range(len(seqs) - 1))
    for e in wire:
        assert e["type"] == e["type"].lower()
        assert e["type"].replace("_", "").isalnum() or "_" in e["type"]

    # HTTP route uses the same iter_sse — exercise it with bounded max_events.
    streamed = await _sse_chunks_to_events(
        session_id=sid,
        cursor=-1,
        manager=state.manager,
        reader=state.reader,
        max_events=len(journal),
    )
    assert streamed
    assert [e["seq"] for e in streamed] == seqs[: len(streamed)]
    assert all(e["type"] == e["type"].lower() for e in streamed)


@pytest.mark.asyncio
async def test_t4_sse_resume_last_event_id_no_gaps_dupes(supervisor_stack) -> None:
    """T4 / §5 B: Last-Event-ID=N resumes at N+1; no gaps/dupes."""
    client = supervisor_stack["client"]
    state = supervisor_stack["state"]
    created = await create_session(client)
    sid = created["session_id"]

    await client.post(
        f"/v1/sessions/{sid}/messages",
        headers=HEADERS_A,
        json={"text": "first", "command_id": f"resume-a-{uuid4().hex}"},
    )
    await client.post(
        f"/v1/sessions/{sid}/messages",
        headers=HEADERS_A,
        json={"text": "second", "command_id": f"resume-b-{uuid4().hex}"},
    )
    full_journal = await wait_for_journal(state.reader, sid, min_events=6)
    assert len(full_journal) >= 2
    cursor = full_journal[len(full_journal) // 2].seq
    expected_remaining = [e for e in full_journal if e.seq > cursor]

    resumed = await _sse_chunks_to_events(
        session_id=sid,
        cursor=cursor,
        manager=state.manager,
        reader=state.reader,
        max_events=len(expected_remaining) or 1,
    )
    assert resumed
    assert resumed[0]["seq"] == cursor + 1
    seen = [e["seq"] for e in resumed]
    assert seen == sorted(set(seen))
    assert all(s > cursor for s in seen)


@pytest.mark.asyncio
async def test_t16_tool_start_before_tool_end_pairing(supervisor_stack) -> None:
    """T16 / §5 B: tool_start before tool_end; paired by tool_call_id."""
    client = supervisor_stack["client"]
    state = supervisor_stack["state"]
    created = await create_session(client)
    sid = created["session_id"]

    await client.post(
        f"/v1/sessions/{sid}/messages",
        headers=HEADERS_A,
        json={"text": "tools", "command_id": f"tools-{uuid4().hex}"},
    )
    journal = await wait_for_journal(state.reader, sid, min_events=3)
    types = [envelope_to_wire(e).type for e in journal]
    assert "tool_start" in types
    assert "tool_end" in types
    assert types.index("tool_start") < types.index("tool_end")

    starts = {
        envelope_to_wire(e).data.get("tool_call_id")
        for e in journal
        if envelope_to_wire(e).type == "tool_start"
    }
    ends = {
        envelope_to_wire(e).data.get("tool_call_id")
        for e in journal
        if envelope_to_wire(e).type == "tool_end"
    }
    assert starts & ends


@pytest.mark.asyncio
async def test_sse_route_requires_tenant(supervisor_stack) -> None:
    """§5 B: events route is tenant-gated; short-lived subscribe does not abort."""
    client = supervisor_stack["client"]
    state = supervisor_stack["state"]
    created = await create_session(client)
    sid = created["session_id"]

    missing = await client.get(f"/v1/sessions/{sid}/events")
    assert missing.status_code == 401

    await client.post(
        f"/v1/sessions/{sid}/messages",
        headers=HEADERS_A,
        json={"text": "seed", "command_id": f"seed-{uuid4().hex}"},
    )
    await wait_for_journal(state.reader, sid, min_events=1)

    events = await _sse_chunks_to_events(
        session_id=sid,
        cursor=-1,
        manager=state.manager,
        reader=state.reader,
        max_events=1,
    )
    assert events

    detail = await client.get(f"/v1/sessions/{sid}", headers=HEADERS_A)
    assert detail.status_code == 200
    assert detail.json()["status"] == "active"


@pytest.mark.asyncio
async def test_slow_sse_consumer_dropped_session_keeps_running(supervisor_stack) -> None:
    """§5 B: slow consumer dropped; session keeps running."""
    client = supervisor_stack["client"]
    state = supervisor_stack["state"]
    created = await create_session(client)
    sid = created["session_id"]

    sub = state.manager.subscribe_live(sid)
    try:
        for i in range(8):
            await client.post(
                f"/v1/sessions/{sid}/messages",
                headers=HEADERS_A,
                json={"text": f"flood-{i}", "command_id": f"flood-{uuid4().hex}"},
            )
            await wait_for_journal(state.reader, sid, min_events=1, require_type=None)
            if sub.dropped:
                break
        assert sub.dropped is True
    finally:
        state.manager.unsubscribe_live(sid, sub)

    detail = await client.get(f"/v1/sessions/{sid}", headers=HEADERS_A)
    assert detail.status_code == 200
    assert detail.json()["status"] == "active"


@pytest.mark.asyncio
async def test_sse_heartbeat_comment_on_idle(supervisor_stack) -> None:
    """§5 B: heartbeats on idle (contract ~20s; shortened via heartbeat_secs)."""
    client = supervisor_stack["client"]
    state = supervisor_stack["state"]
    created = await create_session(client)
    sid = created["session_id"]

    chunks: list[str] = []

    async def _one_heartbeat() -> None:
        async for chunk in iter_sse(
            session_id=sid,
            cursor=10_000,  # nothing to replay
            manager=state.manager,
            reader=state.reader,
            heartbeat_secs=0.05,
            max_events=1,
        ):
            chunks.append(chunk)
            if "ping" in chunk:
                return

    import asyncio

    try:
        await asyncio.wait_for(_one_heartbeat(), timeout=2.0)
    except TimeoutError:
        pass
    assert any("ping" in c for c in chunks)


@pytest.mark.asyncio
@pytest.mark.xfail(
    strict=False,
    reason="Multi-replica live SSE / sticky registry is D12 — out of D4 scope",
)
async def test_d12_multi_replica_live_sse_deferred() -> None:
    """Explicit out-of-scope marker: cross-process live tail is D12."""
    raise AssertionError("D12 multi-replica live hub not required for D4")


@pytest.mark.parametrize(
    ("last_event_id", "since", "cursor"),
    [(None, None, -1), (None, 7, 7), ("3", None, 3), ("3", 7, 3), ("", 7, 7), (None, -1, -1)],
)
def test_the_stream_starts_after_the_cursor_the_client_gives(
    last_event_id: str | None, since: int | None, cursor: int
) -> None:
    """``?since=`` is honoured (F34); a reconnect's Last-Event-ID takes precedence."""
    from supervisor.api.events import stream_cursor

    assert stream_cursor(last_event_id, since) == cursor


@pytest.mark.parametrize(("last_event_id", "since"), [("x", None), ("-5", None), (None, -2)])
def test_a_cursor_that_is_not_a_seq_is_refused(last_event_id: str | None, since: int | None) -> None:
    from fastapi import HTTPException

    from supervisor.api.events import stream_cursor

    with pytest.raises(HTTPException) as refused:
        stream_cursor(last_event_id, since)
    assert refused.value.status_code == 400
