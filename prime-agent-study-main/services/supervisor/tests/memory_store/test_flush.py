"""A flush makes durable what it read, and nothing else is lost: an event appended while it
runs stays for the next flush, and a second flush of the session waits instead of failing."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from supervisor.memory_service.app import _map_error
from supervisor.memory_service.service import MemoryService
from supervisor.memory_service.store import SeqConflict
from supervisor.memory_service.types import Scope

SC = Scope("t1", "user:alice", "agent")


async def _svc(tmp_path: Path) -> MemoryService:
    svc = MemoryService(database_url=f"sqlite+aiosqlite:///{tmp_path / 'm.db'}")
    await svc.startup()
    return svc


@pytest.mark.asyncio
async def test_an_event_appended_during_a_flush_is_kept(tmp_path: Path) -> None:
    svc = await _svc(tmp_path)
    h = await svc.create_session(SC)
    svc.append(h.id, "user/message", {"text": "one"})
    real = svc._store.flush_events

    async def slow(*args: object, **kwargs: object) -> int:
        svc.append(h.id, "assistant/message", {"text": "two"})  # while the flush is in flight
        return await real(*args, **kwargs)  # type: ignore[arg-type]

    svc._store.flush_events = slow  # type: ignore[method-assign]
    await svc.flush(h.id)
    svc._store.flush_events = real  # type: ignore[method-assign]
    assert svc.pending_count(h.id) == 1
    await svc.flush(h.id)
    said = [e.data["text"] for e in (await svc.load(h.id)).events if "text" in e.data]
    assert said == ["one", "two"]
    await svc.aclose()


@pytest.mark.asyncio
async def test_two_flushes_at_once_write_each_event_once(tmp_path: Path) -> None:
    svc = await _svc(tmp_path)
    h = await svc.create_session(SC)
    for i in range(5):
        svc.append(h.id, "user/message", {"text": f"m{i}"})
    lasts = await asyncio.gather(svc.flush(h.id), svc.flush(h.id))
    assert lasts == [4, 4]
    said = [e.data["text"] for e in (await svc.load(h.id)).events if "text" in e.data]
    assert said == [f"m{i}" for i in range(5)], "each once, in order"
    await svc.aclose()


def test_a_seq_conflict_is_a_conflict_not_an_internal_error() -> None:
    answer = _map_error(SeqConflict("SEQ_CONFLICT: expected 5, got 3", code="SEQ_CONFLICT"))
    assert answer.status_code == 409 and answer.detail["code"] == "SEQ_CONFLICT"  # type: ignore[index]
