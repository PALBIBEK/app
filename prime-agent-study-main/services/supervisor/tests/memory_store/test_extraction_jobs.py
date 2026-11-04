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
    assert svc.background == ()


class _Flaky:
    """Fails its first ``failures`` runs, then extracts nothing."""

    def __init__(self, failures: int) -> None:
        self.failures, self.runs = failures, 0

    def extract(self, events: list[object]) -> list[object]:
        self.runs += 1
        if self.runs <= self.failures:
            raise RuntimeError(f"model gateway timed out (run {self.runs})")
        return []


async def _due_now(svc: MemoryService) -> None:
    async with svc._factory() as db, db.begin():
        await db.execute(update(ExtractionJobRow).values(retry_at_ms=0))


@pytest.mark.asyncio
async def test_a_failed_job_is_retried_after_a_backoff(url: str) -> None:
    flaky = _Flaky(failures=2)
    svc = MemoryService(database_url=url, extractor=flaky)  # type: ignore[arg-type]
    await svc.startup()
    h = await svc.create_session(SC)
    await _turn(svc, h.id, "we decided to use postgres for storage")
    assert await svc.process_extraction_jobs() == 0
    (job,) = await _jobs(svc)
    assert (job.status, job.attempts) == ("pending", 1) and job.retry_at_ms is not None
    assert "timed out" in (job.error or "")
    assert await svc.process_extraction_jobs() == 0 and flaky.runs == 1, "not due yet: backing off"
    for _ in range(2):
        await _due_now(svc)
        await svc.process_extraction_jobs()
    (job,) = await _jobs(svc)
    assert (job.status, job.attempts, flaky.runs) == ("done", 3, 3)
    await svc.aclose()


@pytest.mark.asyncio
async def test_a_job_that_keeps_failing_stops_and_can_be_requeued(url: str) -> None:
    from supervisor.memory_service.extract import MAX_ATTEMPTS, retry_delay_ms

    flaky = _Flaky(failures=10**6)
    svc = MemoryService(database_url=url, extractor=flaky)  # type: ignore[arg-type]
    await svc.startup()
    h = await svc.create_session(SC)
    await _turn(svc, h.id, "we decided to use postgres for storage")
    for _ in range(MAX_ATTEMPTS + 2):
        await _due_now(svc)
        await svc.process_extraction_jobs()
    (job,) = await _jobs(svc)
    assert (job.status, job.attempts, flaky.runs) == ("error", MAX_ATTEMPTS, MAX_ATTEMPTS)
    assert (await svc.metrics_snapshot())["extraction_jobs_failed"] == 1
    assert await svc.retry_failed_extraction_jobs() == 1
    (job,) = await _jobs(svc)
    assert (job.status, job.attempts) == ("pending", 0)
    assert [retry_delay_ms(n) for n in (1, 2, 3)] == [30_000, 60_000, 120_000]
    assert retry_delay_ms(50) == 60 * 60_000
    await svc.aclose()


@pytest.mark.asyncio
async def test_a_job_whose_runs_all_crashed_their_process_is_given_up(url: str) -> None:
    from supervisor.memory_service.extract import MAX_ATTEMPTS

    svc = await _svc(url)
    h = await svc.create_session(SC)
    await _turn(svc, h.id, "we decided to use postgres for storage")
    async with svc._factory() as db, db.begin():  # claimed for its last run, then the process died
        await db.execute(update(ExtractionJobRow).values(status="running", claimed_at_ms=1, attempts=MAX_ATTEMPTS))
    assert await svc.process_extraction_jobs() == 0
    (job,) = await _jobs(svc)
    assert job.status == "error"
    await svc.aclose()
