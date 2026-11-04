"""Extraction jobs: a job runs once however many processors race for it, a crashed
processor's job is retaken, the cursor survives a restart, a worker drains the queue,
and the backlog is visible in the metrics."""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import select, update

from supervisor.memory_service.extract import CLAIM_TIMEOUT_MS
from supervisor.memory_service.models import ExtractionJobRow
from supervisor.memory_service.service import MemoryService
from supervisor.memory_service.types import Scope

SC = Scope("t1", "user:alice", "agent")


async def _svc(url: str) -> MemoryService:
    svc = MemoryService(database_url=url)
    await svc.startup()
    return svc


async def _turn(svc: MemoryService, session_id: str, text: str) -> None:
    svc.append(session_id, "user/message", {"text": text})
    svc.append(session_id, "turn/end", {})
    await svc.flush(session_id)


async def _jobs(svc: MemoryService) -> list[ExtractionJobRow]:
    async with svc._factory() as db:
        rows = (await db.execute(select(ExtractionJobRow).order_by(ExtractionJobRow.created_at_ms))).scalars()
        return list(rows)


@pytest.mark.asyncio
async def test_two_processors_run_a_job_once(url: str) -> None:
    a, b = await _svc(url), await _svc(url)
    h = await a.create_session(SC)
    await _turn(a, h.id, "we decided to use postgres for storage")
    ran = await asyncio.gather(a.process_extraction_jobs(), b.process_extraction_jobs())
    assert sum(ran) == 1
    assert [j.status for j in await _jobs(a)] == ["done"]
    await b.aclose()
    await a.aclose()


@pytest.mark.asyncio
async def test_a_job_claimed_by_a_processor_that_died_is_retaken(url: str) -> None:
    svc = await _svc(url)
    h = await svc.create_session(SC)
    await _turn(svc, h.id, "we decided to use postgres for storage")
    async with svc._factory() as db, db.begin():  # a processor claimed it, then died
        await db.execute(update(ExtractionJobRow).values(status="running", claimed_at_ms=1))
    assert await svc.process_extraction_jobs() == 1
    async with svc._factory() as db, db.begin():  # a live claim is left alone
        await db.execute(update(ExtractionJobRow).values(status="running", claimed_at_ms=10**15))
    assert await svc.process_extraction_jobs() == 0
    assert CLAIM_TIMEOUT_MS > 0
    await svc.aclose()


@pytest.mark.asyncio
async def test_after_a_restart_a_job_starts_after_the_last_one(url: str) -> None:
    svc = await _svc(url)
    h = await svc.create_session(SC)
    await _turn(svc, h.id, "I prefer tabs over spaces")
    restarted = await _svc(url)
    await restarted.load(h.id)
    await _turn(restarted, h.id, "we decided to use postgres")
    assert [(j.seq_from, j.seq_to) for j in await _jobs(svc)] == [(0, 1), (2, 3)]
    await restarted.aclose()
    await svc.aclose()


@pytest.mark.asyncio
async def test_the_worker_drains_the_queue_and_the_backlog_is_reported(url: str) -> None:
    svc = await _svc(url)
    h = await svc.create_session(SC)
    await _turn(svc, h.id, "we decided to use postgres for storage")
    waiting = await svc.metrics_snapshot()
    assert waiting["extraction_backlog"] == 1
    assert waiting["extraction_oldest_waiting_ms"] >= 0
    svc.start_extraction_worker(interval_s=0.01)
    for _ in range(200):
        if (await svc.metrics_snapshot())["extraction_backlog"] == 0:
            break
        await asyncio.sleep(0.01)
    assert (await svc.metrics_snapshot())["extraction_backlog"] == 0
    assert [j.status for j in await _jobs(svc)] == ["done"]
    await svc.aclose()  # stops the worker
    assert svc._extraction_worker is None
