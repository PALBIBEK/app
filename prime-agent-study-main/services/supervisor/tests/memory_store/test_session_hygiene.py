"""Session log hygiene: idle buffers are flushed or dropped, a buffer has a capacity, a
duplicate session id is a conflict and a fork point must be inside the session."""

from __future__ import annotations

import asyncio

import pytest

from supervisor.memory_service.app import _map_error
from supervisor.memory_service.buffer import WriteBehindBuffer
from supervisor.memory_service.service import MemoryService
from supervisor.memory_service.types import BufferFull, InvalidRequest, Scope, SessionExists

SC = Scope("t1", "user:alice", "agent")


async def _svc(url: str) -> MemoryService:
    svc = MemoryService(database_url=url)
    await svc.startup()
    return svc


@pytest.mark.asyncio
async def test_the_sweep_flushes_idle_buffers_and_drops_those_of_no_session(url: str) -> None:
    svc = await _svc(url)
    h = await svc.create_session(SC)
    svc.append(h.id, "user/message", {"text": "said, never checkpointed"})
    for i in range(50):
        svc.append(f"no-such-session-{i}", "user/message", {"text": "x"})
    fresh = await svc.create_session(SC)
    svc.append(fresh.id, "user/message", {"text": "being written now"})
    for sid in [h.id, *(f"no-such-session-{i}" for i in range(50))]:
        svc._buffers[sid].touched_ms = 0  # idle for ages
    out = await svc.sweep_buffers()
    assert out == {"flushed": 1, "dropped": 51}
    assert [e.data["text"] for e in (await svc.load(h.id)).events] == ["said, never checkpointed"]
    assert set(svc._buffers) == {h.id, fresh.id}, "load re-seeds the swept session's buffer"
    assert svc.pending_count(fresh.id) == 1, "a buffer in use is left alone"
    await svc.aclose()


@pytest.mark.asyncio
async def test_the_sweeper_runs_in_the_background(url: str) -> None:
    svc = await _svc(url)
    svc.append("gone", "user/message", {"text": "x"})
    svc.start_buffer_sweeper(interval_s=0.01, idle_ms=0)
    for _ in range(200):
        if "gone" not in svc._buffers:
            break
        await asyncio.sleep(0.01)
    assert "gone" not in svc._buffers
    await svc.aclose()
    assert svc._buffer_sweeper is None


def test_a_full_buffer_refuses_appends_until_flushed() -> None:
    buf = WriteBehindBuffer(capacity=2)
    buf.append("a")
    buf.append("b")
    with pytest.raises(BufferFull):
        buf.append("c")
    buf.discard(1)
    assert buf.append("c").seq == 2


@pytest.mark.asyncio
async def test_a_duplicate_session_id_is_a_conflict(url: str) -> None:
    svc = await _svc(url)
    await svc.create_session(SC, session_id="fixed")
    with pytest.raises(SessionExists):
        await svc.create_session(SC, session_id="fixed")
    raced = await asyncio.gather(
        *(svc.create_session(SC, session_id="raced") for _ in range(5)), return_exceptions=True
    )
    assert sum(not isinstance(r, BaseException) for r in raced) == 1
    assert all(isinstance(r, SessionExists) for r in raced if isinstance(r, BaseException))
    await svc.aclose()


@pytest.mark.asyncio
async def test_a_fork_point_must_be_inside_the_session(url: str) -> None:
    svc = await _svc(url)
    h = await svc.create_session(SC)
    svc.append(h.id, "user/message", {"text": "x"})
    svc.append(h.id, "user/message", {"text": "y"})
    await svc.flush(h.id)
    for at in (2, 10_000, -5):
        with pytest.raises(InvalidRequest):
            await svc.fork(h.id, at_seq=at)
    before = await svc.fork(h.id, at_seq=-1)
    assert (await svc.load(before.id)).events == ()
    first = await svc.fork(h.id, at_seq=0)
    assert [e.data["text"] for e in (await svc.load(first.id)).events] == ["x"]
    await svc.aclose()


def test_the_errors_are_answers() -> None:
    assert _map_error(SessionExists("SESSION_EXISTS: x")).status_code == 409
    assert _map_error(BufferFull("BUFFER_FULL")).status_code == 409
    assert _map_error(InvalidRequest("INVALID_REQUEST")).status_code == 422
