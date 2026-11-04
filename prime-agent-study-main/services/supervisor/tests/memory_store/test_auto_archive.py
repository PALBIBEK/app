"""Sessions idle for ``hot_days`` move out of the hot table on their own, except those
something still needs hot: a live lease, pending extraction or outbox work."""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import func, select

from supervisor.memory_service.models import SessionEventRow
from supervisor.memory_service.service import MemoryService
from supervisor.memory_service.types import Scope
from tests.support.memory_extraction import scripted_extractor

SC = Scope("t1", "user:alice", "agent")
LONG_AGO = 1_000  # ms since the epoch: far older than any hot window


async def _session(svc: MemoryService, *, at: int | None, text: str = "hello") -> str:
    h = await svc.create_session(SC)
    svc.append(h.id, "user/message", {"text": text}, time_ms=at)
    await svc.flush(h.id)
    return h.id


async def _hot_rows(svc: MemoryService, session_id: str) -> int:
    async with svc._factory() as db:
        query = select(func.count()).select_from(SessionEventRow).where(SessionEventRow.session_id == session_id)
        return int(await db.scalar(query) or 0)


@pytest.mark.asyncio
async def test_only_idle_sessions_nothing_needs_are_archived(url: str) -> None:
    svc = MemoryService(database_url=url, hot_days=30, extractor=scripted_extractor())
    await svc.startup()
    cold = await _session(svc, at=LONG_AGO)
    recent = await _session(svc, at=None)
    leased = await _session(svc, at=LONG_AGO)
    await svc.acquire_lease(leased, "worker-a")
    extracting = await svc.create_session(SC)  # a turn whose extraction has not run yet
    svc.append(extracting.id, "user/message", {"text": "we decided to use postgres"}, time_ms=LONG_AGO)
    svc.append(extracting.id, "turn/end", {}, time_ms=LONG_AGO)
    await svc.flush(extracting.id)
    side_effect = await svc.create_session(SC)  # a side effect not yet executed
    svc.append(side_effect.id, "assistant/message", {"text": "paying"}, time_ms=LONG_AGO)
    svc.append(side_effect.id, "tool/call", {"call_id": "c1", "name": "pay", "side_effect": True}, time_ms=LONG_AGO)
    await svc.flush(side_effect.id)
    erased = await _session(svc, at=LONG_AGO)
    await svc._store.set_status(erased, "erased")

    assert await svc._retention.cold_sessions() == [cold]
    assert await svc.archive_cold_sessions() == 1
    assert await _hot_rows(svc, cold) == 0
    loaded = await svc.load(cold)
    assert (loaded.status, [e.data["text"] for e in loaded.events]) == ("archived", ["hello"])
    for kept in (recent, leased, extracting.id, side_effect.id, erased):
        assert await _hot_rows(svc, kept) > 0
    assert await svc.archive_cold_sessions() == 0, "nothing left to do"

    await svc.process_extraction_jobs()  # its extraction done, the session may go
    assert extracting.id in await svc._retention.cold_sessions()
    await svc.aclose()


@pytest.mark.asyncio
async def test_the_archiver_runs_in_the_background(url: str) -> None:
    svc = MemoryService(database_url=url, hot_days=30, extractor=scripted_extractor())
    await svc.startup()
    cold = await _session(svc, at=LONG_AGO)
    svc.start_archiver(interval_s=0.01)
    assert "archive" in svc.background
    for _ in range(200):
        if not await _hot_rows(svc, cold):
            break
        await asyncio.sleep(0.01)
    assert await _hot_rows(svc, cold) == 0
    await svc.aclose()
    assert svc.background == ()
