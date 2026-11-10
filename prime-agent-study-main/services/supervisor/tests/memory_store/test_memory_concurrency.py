"""Memory under the concurrency the platform runs (``docs/memory-gaps.md``, G0): many
sessions and workers, on memory-service replicas that share one Postgres.

The baseline every memory gap builds on, and the harness's own proof that it makes
writers overlap: replicas that boot together create one schema, a notebook ensured
from everywhere at once is one notebook, concurrent captures into one notebook all land
exactly once, a capture waits only on its own notebook's lock, and extraction jobs claimed by
many processors each run once.
"""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import create_async_engine

from supervisor.memory_service.models import ExtractionJobRow
from supervisor.memory_service.notebooks.models import NotebookRow
from supervisor.memory_service.notebooks.types import CaptureMetadata, Sensitivity
from supervisor.memory_service.service import MemoryService
from supervisor.memory_service.types import Scope
from tests.support.memory_extraction import scripted_extractor
from tests.support.memory_pg import Replicas, run_workers

pytestmark = pytest.mark.asyncio

PIN = "composition:githi@sha256:race"
OWN = CaptureMetadata(sensitivity=Sensitivity.ORDINARY, inherited=())
REPLICAS = 4
WORKERS = 40


async def test_workers_are_released_together_and_all_finish_before_failures_are_raised() -> None:
    """The harness itself (no database): every worker overlaps the others, and one that
    fails does not leave the rest running."""
    running, peak, finished = 0, 0, 0

    async def work(i: int) -> int:
        nonlocal running, peak, finished
        running += 1
        peak = max(peak, running)
        await asyncio.sleep(0.01)
        running -= 1
        finished += 1
        if i == 3:
            raise ValueError("worker 3")
        return i

    with pytest.raises(BaseExceptionGroup) as failed:
        await run_workers(8, work)

    assert peak == 8, "all eight ran at once"
    assert finished == 8, "the failure waited for every worker"
    assert [str(e) for e in failed.value.exceptions] == ["worker 3"]
    assert await run_workers(5, lambda i: asyncio.sleep(0, result=i * i)) == [0, 1, 4, 9, 16]


async def test_replicas_booting_together_share_one_schema(replicas: Replicas) -> None:
    started = await replicas.start(REPLICAS)  # each bootstraps the schema, at once

    made = await run_workers(
        REPLICAS,
        lambda i: started[i].client().ensure(tenant_id="t1", owner=f"u{i}", agent_pin=PIN, name="default"),
    )
    await run_workers(REPLICAS, lambda i: started[i].client().capture(made[i].id, [f"Made on replica {i}"], context=OWN))

    for i in range(REPLICAS):  # what one replica wrote, every other replica reads
        reader = started[(i + 1) % REPLICAS].client()
        assert (await reader.get(made[i].id)).id == made[i].id
        assert [f.text for f in await reader.facts(made[i].id)] == [f"Made on replica {i}"]


async def test_one_notebook_ensured_from_everywhere_at_once_is_one_notebook(replicas: Replicas) -> None:
    await replicas.start(REPLICAS)
    clients = replicas.clients(WORKERS)

    made = await run_workers(
        WORKERS, lambda i: clients[i].ensure(tenant_id="t1", owner="alice", agent_pin=PIN, name="default")
    )

    assert len({nb.id for nb in made}) == 1
    listed = await clients[0].list_notebooks(tenant_id="t1", owner="alice", agent_pin=PIN)
    assert [nb.name for nb in listed] == ["default"]


async def test_concurrent_captures_into_one_notebook_all_land_exactly_once(replicas: Replicas) -> None:
    """Forty sessions on four replicas capture into one notebook at once: each its own
    fact, and all of them one fact they share. Every fact lands once, every capture is
    one revision, and the shared fact is not duplicated by the race."""
    await replicas.start(REPLICAS)
    clients = replicas.clients(WORKERS)
    nb = await clients[0].ensure(tenant_id="t1", owner="alice", agent_pin=PIN, name="default")

    added = await run_workers(
        WORKERS, lambda i: clients[i].capture(nb.id, [f"Owns service {i:02d}", "Prefers tea"], context=OWN)
    )

    facts = [f.text for f in await clients[0].facts(nb.id)]
    assert sorted(facts) == sorted([f"Owns service {i:02d}" for i in range(WORKERS)] + ["Prefers tea"])
    assert sum(added) == WORKERS + 1, "the shared fact counted once, by whichever capture won"
    history = await clients[0].history(nb.id)
    assert len({r.revision for r in history}) == WORKERS, "one revision per capture, none lost"
    assert [r.op for r in history if r.text == "Prefers tea"] == ["insert"], "the shared fact inserted once"


async def test_a_capture_waits_only_on_its_own_notebooks_lock(replicas: Replicas) -> None:
    """Hot spot S1 as it is today: every write to a notebook takes that notebook's row
    lock. While another transaction holds notebook A's, a capture into A waits and a
    capture into B does not; releasing A lets the waiting capture land. A shared notebook
    therefore serializes its writers, which the inbox (G2/G5) is to remove; until then this
    pins the lock to one notebook, never a global one."""
    await replicas.start(2)
    first, second = replicas.clients(2)
    a = await first.ensure(tenant_id="t1", owner="alice", agent_pin=PIN, name="default")
    b = await first.ensure(tenant_id="t1", owner="bob", agent_pin=PIN, name="default")
    engine = create_async_engine(replicas.url)
    try:
        async with engine.connect() as holder:
            await holder.execute(
                update(NotebookRow).where(NotebookRow.id == a.id).values(revision=NotebookRow.revision)
            )  # the same no-op write that is the store's lock, held open
            waiting = asyncio.create_task(first.capture(a.id, ["Waits for the lock"], context=OWN))
            try:
                assert await asyncio.wait_for(second.capture(b.id, ["Never waits"], context=OWN), 10) == 1
                await asyncio.sleep(0.5)
                assert not waiting.done(), "a capture into the locked notebook waits"
            finally:
                # Whatever failed: release the lock first, so the service finishes the waiting
                # capture (cancelling only the client would leave it running server-side
                # into the database's teardown), then wait for it.
                await holder.rollback()
                landed = await asyncio.wait_for(asyncio.gather(waiting, return_exceptions=True), 10)
            assert landed == [1]
    finally:
        await engine.dispose()
    assert [f.text for f in await first.facts(a.id)] == ["Waits for the lock"]


async def test_extraction_jobs_claimed_by_many_processors_each_run_once(replicas: Replicas) -> None:
    """Eight processors (the extraction half of eight replicas, without their apps) drain
    twenty sessions' jobs at once, three at a time, so their claims contend round after
    round: every job runs exactly once and ends done."""
    made = [MemoryService(database_url=replicas.url, extractor=scripted_extractor()) for _ in range(8)]
    booted = await asyncio.gather(*(p.startup() for p in made), return_exceptions=True)
    processors = [p for p, b in zip(made, booted, strict=True) if not isinstance(b, BaseException)]
    try:
        if len(processors) < len(made):  # every boot settled; the ones that started are closed below
            raise BaseExceptionGroup("processors did not start", [b for b in booted if isinstance(b, BaseException)])
        scope = Scope("t1", "user:alice", "agent")
        for n in range(20):
            handle = await processors[n % 8].create_session(scope)
            processors[n % 8].append(handle.id, "user/message", {"text": f"we chose option {n} for storage"})
            processors[n % 8].append(handle.id, "turn/end", {})
            await processors[n % 8].flush(handle.id)

        async def drain(i: int) -> int:
            ran = 0
            while batch := await processors[i].process_extraction_jobs(limit=3):
                ran += batch
            return ran

        ran = await run_workers(8, drain)

        engine = create_async_engine(replicas.url)
        try:
            async with engine.connect() as conn:
                statuses = dict(
                    (await conn.execute(select(ExtractionJobRow.status, func.count()).group_by(ExtractionJobRow.status))).all()
                )
        finally:
            await engine.dispose()
        backlog = await processors[0].metrics_snapshot()
        assert sum(ran) == 20, f"each job ran once: {ran}"
        assert statuses == {"done": 20}
        assert backlog["extraction_backlog"] == backlog["extraction_jobs_failed"] == 0
    finally:
        await asyncio.gather(*(p.aclose() for p in processors))
