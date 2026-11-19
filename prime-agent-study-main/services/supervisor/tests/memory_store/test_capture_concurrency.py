"""G3 at the platform's concurrency (``docs/memory-gaps.md``): the capture queue drained by
many workers through memory-service replicas sharing one Postgres.

Every job runs exactly once and no cursor moves backwards; a worker paused past its lease
is fenced when it wakes; captures from many sessions resolved against one notebook keep one
fact, never a duplicate; one tenant's backlog of ten thousand jobs never holds another
tenant's up beyond its fair share; and replicas commit sessions into one notebook
independently (Grok's ``independent_handles`` test).
"""

from __future__ import annotations

import asyncio
import collections
import time
from typing import Any

import pytest
from sqlalchemy import insert, select
from sqlalchemy.ext.asyncio import create_async_engine

from supervisor.memory.client import NotebookClient
from supervisor.memory_service.capture.errors import StaleLease, StaleResolution
from supervisor.memory_service.capture.models import CaptureJobRow, CaptureSessionRow
from supervisor.memory_service.capture.queue import CaptureLease, job_id_for
from supervisor.memory_service.notebooks.types import CaptureMetadata, Fact, Sensitivity
from supervisor.memory_service.notebooks.wire import observation_out
from supervisor.memory_service.observations import PROMPT_VERSION, ObservationDraft, ObservationType
from tests.support.memory_pg import Replicas, run_workers

pytestmark = pytest.mark.asyncio

PIN = "composition:githi@sha256:capture-race"
OWN = {"sensitivity": Sensitivity.ORDINARY.value, "inherited": []}
NOOP: dict[str, Any] = {"observations": [], "retire": []}


@pytest.fixture(autouse=True)
def _burst_allowed(monkeypatch: pytest.MonkeyPatch) -> None:
    """Many workers call at once for few tenants: room for them in each replica's rate
    limit (``memory_service.rate_limit``), which is not what is under test here."""
    monkeypatch.setenv("MEMORY_RATE_LIMIT_PER_SECOND", "100000")
    monkeypatch.setenv("MEMORY_RATE_LIMIT_BURST", "100000")


def _outcome(*statements: str, retire: tuple[str, ...] = ()) -> dict[str, Any]:
    drafts = [
        observation_out(
            ObservationDraft(
                observation_type=ObservationType.PROJECT,
                topic_hint="capture",
                statement=s,
                keywords=(),
                aliases=(),
                extraction_model="model",
                prompt_version=PROMPT_VERSION,
                created_at=1,
                body=None,
            )
        )
        for s in statements
    ]
    return {"observations": drafts, "retire": list(retire), "context": OWN}


async def _notebook(client: NotebookClient, tenant: str) -> str:
    return (await client.ensure(tenant_id=tenant, owner="alice", agent_pin=PIN, name="default")).id


async def _landed(client: NotebookClient, notebook_id: str, count: int, timeout: float = 60) -> list[Fact]:
    async with asyncio.timeout(timeout):
        while len(facts := await client.facts(notebook_id)) < count:
            await asyncio.sleep(0.05)
    return facts


async def _indexed(client: NotebookClient, tenant: str, sessions: list[str], through: int) -> None:
    for sid in sessions:
        barrier = await client.capture_flush(tenant_id=tenant, session_id=sid, through_turn=through, timeout_s=60)
        assert barrier["result"] == "success", (sid, barrier)


async def test_eight_workers_run_five_hundred_jobs_exactly_once_and_no_cursor_moves_back(replicas: Replicas) -> None:
    await replicas.start(4)
    tenants, sessions_each, bursts = ("t1", "t2"), 25, 10
    queuers = replicas.clients(2 * sessions_each)
    notebooks = {t: await _notebook(queuers[0], t) for t in tenants}
    sessions = [(t, f"{t}-s{i:02d}") for t in tenants for i in range(sessions_each)]

    async def queue_session(i: int) -> None:  # a session queues its bursts in order, all sessions at once
        tenant, sid = sessions[i]
        for n in range(1, bursts + 1):
            job = await queuers[i].capture_enqueue(tenant_id=tenant, session_id=sid, notebook_id=notebooks[tenant], run_ids=[f"r{n}"])
            assert (job.from_turn, job.through_turn) == (n, n)

    await run_workers(len(sessions), queue_session)
    total = len(sessions) * bursts

    claimed: collections.Counter[str] = collections.Counter()
    seen: dict[str, tuple[int, int, int]] = {}
    done = asyncio.Event()
    engine = create_async_engine(replicas.url)

    async def watch() -> None:  # every cursor, read straight from the shared database, as it moves
        async with engine.connect() as conn:
            while not done.is_set():
                rows = (await conn.execute(select(CaptureSessionRow.session_id, CaptureSessionRow.requested,
                                                  CaptureSessionRow.captured, CaptureSessionRow.indexed))).all()
                await conn.rollback()
                for sid, requested, captured, indexed in rows:
                    before = seen.get(sid, (0, 0, 0))
                    now = (int(requested), int(captured), int(indexed))
                    assert all(b <= a for b, a in zip(before, now, strict=True)), f"{sid} moved back: {before} -> {now}"
                    assert now[2] <= now[1] <= now[0], f"{sid}: indexed <= captured <= requested, not {now}"
                    seen[sid] = now
                await asyncio.sleep(0.01)

    workers = replicas.clients(8)

    async def work(i: int) -> None:
        client = workers[i]
        while sum(claimed.values()) < total:
            leases = []
            for tenant in await client.capture_tenants(limit=4):
                if (lease := await client.capture_claim(tenant_id=tenant, owner=f"w{i}", lease_s=600)) is not None:
                    leases.append(lease)
            if not leases:
                await asyncio.sleep(0.005)
            for lease in leases:
                claimed[lease.job.job_id] += 1
                n = lease.job.through_turn
                outcome = _outcome(f"{lease.job.session_id} learned {n}") if n % 5 == 0 else NOOP
                assert await client.capture_commit(lease, outcome) is False

    watcher = asyncio.create_task(watch())
    try:
        began = time.monotonic()
        await asyncio.wait_for(run_workers(len(workers), work), 120)
        took = time.monotonic() - began
        for tenant in tenants:
            await _indexed(queuers[0], tenant, [s for t, s in sessions if t == tenant], bursts)
    finally:
        done.set()
        await watcher
        await engine.dispose()

    expected = {job_id_for(sid, [f"r{n}"]) for _, sid in sessions for n in range(1, bursts + 1)}
    assert set(claimed) == expected and set(claimed.values()) == {1}, "every job, each exactly once"
    assert seen and all(v == (bursts, bursts, bursts) for v in seen.values())
    for tenant in tenants:
        facts = await _landed(queuers[0], notebooks[tenant], sessions_each * 2)
        texts = sorted(f.text for f in facts)
        assert texts == sorted(f"{s} learned {n}" for t, s in sessions if t == tenant for n in (5, 10)), "landed once each"
    assert took < 60, f"{total} jobs took {took:.1f}s"


async def test_a_worker_paused_past_its_lease_is_fenced_when_it_wakes(replicas: Replicas) -> None:
    first, second = await replicas.start(2)
    paused, live = first.client(), second.client()
    nb = await _notebook(paused, "t1")
    await paused.capture_enqueue(tenant_id="t1", session_id="s", notebook_id=nb, run_ids=["r1"])
    stale = await paused.capture_claim(tenant_id="t1", owner="paused", lease_s=1)
    assert stale is not None
    await asyncio.sleep(1.2)  # a GC pause, a lost network: its lease runs out
    fresh = await live.capture_claim(tenant_id="t1", owner="live", lease_s=60)
    assert fresh is not None and fresh.job.attempt == stale.job.attempt + 1

    # Both commit at once, on two replicas; the woken one with its own (different) outcome.
    results = await asyncio.gather(
        paused.capture_commit(stale, _outcome("Paused worker's verdict")),
        live.capture_commit(fresh, _outcome("Live worker's verdict")),
        return_exceptions=True,
    )
    assert isinstance(results[0], StaleLease) and results[1] is False
    for late in (paused.capture_fail(stale, "late", terminal=True), paused.capture_release(stale, "late")):
        with pytest.raises(StaleLease):
            await late
    with pytest.raises(StaleLease):
        await paused.capture_commit(stale, _outcome("Live worker's verdict"))  # even the same bytes

    (fact,) = await _landed(live, nb, 1)
    assert fact.text == "Live worker's verdict"
    await _indexed(live, "t1", ["s"], 1)


async def test_sessions_resolving_against_one_notebook_keep_one_fact_and_retire_the_old_once(replicas: Replicas) -> None:
    """S3 at scale: twenty sessions learn the same new fact at once, each resolved against
    the notebook as it read it; each would add it and retire the fact it replaces. One
    commit passes; every other finds the revision changed, resolves again (as the worker's
    ``RESOLVE_ATTEMPTS`` loop does), sees the fact queued or landed, and keeps nothing."""
    await replicas.start(4)
    clients = replicas.clients(20)
    nb = await _notebook(clients[0], "t1")
    await clients[0].capture(nb, ["Deploys on Mondays"], context=CaptureMetadata(sensitivity=Sensitivity.ORDINARY, inherited=()))
    (old,) = await _landed(clients[0], nb, 1)
    leases: list[CaptureLease] = []
    for i, client in enumerate(clients):
        await client.capture_enqueue(tenant_id="t1", session_id=f"s{i}", notebook_id=nb, run_ids=["r1"])
        lease = await client.capture_claim(tenant_id="t1", owner=f"w{i}", lease_s=600, session_id=f"s{i}")
        assert lease is not None
        leases.append(lease)
    seen, _ = await clients[0].capture_view(nb)
    stale = collections.Counter[int]()

    async def resolve(i: int) -> str:
        client = clients[i]
        revision, queued = seen, []  # all read the notebook at once, before any commit
        while True:
            facts = {f.text for f in await client.facts(nb)}  # after the revision, as resolution reads
            knows = "Deploys on Fridays" in facts | {text for text, _ in queued}
            outcome = NOOP if knows else _outcome("Deploys on Fridays", retire=(old.id,))
            try:
                await client.capture_commit(leases[i], outcome, resolved_at=revision)
                return "noop" if knows else "added"
            except StaleResolution:
                stale[i] += 1
                revision, queued = await client.capture_view(nb)

    verdicts = await asyncio.wait_for(run_workers(len(clients), resolve), 60)

    assert sorted(verdicts) == ["added"] + ["noop"] * (len(clients) - 1), "one commit adds it"
    assert sum(stale.values()) >= len(clients) - 1, "every other was refused, then resolved again"
    await _indexed(clients[0], "t1", [f"s{i}" for i in range(len(clients))], 1)
    (fact,) = await _landed(clients[0], nb, 1)
    assert fact.text == "Deploys on Fridays", "no duplicate, and the fact it replaces retired"


async def test_one_tenants_ten_thousand_job_backlog_never_holds_another_tenant_up(replicas: Replicas) -> None:
    """Rule 6 for the queue: workers sweep as every worker's sweeper does (the service's fair
    order, one job per tenant a round), so a tenant queuing later behind a huge backlog is
    served within a round or two, not after it."""
    (replica,) = await replicas.start(1)
    await replicas.start(1)
    queuer = replica.client()
    big, small = await _notebook(queuer, "big"), await _notebook(queuer, "small")
    now = int(time.time() * 1000)
    engine = create_async_engine(replicas.url)
    try:
        async with engine.begin() as conn:  # the backlog, straight into the table: 100 sessions of 100 bursts
            await conn.execute(insert(CaptureSessionRow), [
                {"session_id": f"big-{s}", "tenant_id": "big", "requested": 100, "captured": 0, "indexed": 0, "updated_at": now}
                for s in range(100)
            ])
            await conn.execute(insert(CaptureJobRow), [
                {
                    "job_id": job_id_for(f"big-{s}", [f"r{n}"]), "tenant_id": "big", "session_id": f"big-{s}",
                    "notebook_id": big, "run_ids": [f"r{n}"], "from_turn": n, "through_turn": n, "status": "pending",
                    "terminal": False, "attempt": 0, "available_at": now - 3_600_000 + n, "queued": False,
                    "indexed": False, "created_at": now - 3_600_000, "updated_at": now,
                }
                for s in range(100) for n in range(1, 101)
            ])
    finally:
        await engine.dispose()
    workers = replicas.clients(4)
    order: list[str] = []
    stop = asyncio.Event()

    async def sweep(i: int) -> None:
        client = workers[i]
        while not stop.is_set():
            for tenant in await client.capture_tenants(limit=1):  # this worker's one free slot
                if (lease := await client.capture_claim(tenant_id=tenant, owner=f"w{i}", lease_s=600)) is not None:
                    await asyncio.sleep(0.01)  # extraction takes a while: running jobs are what fairness counts
                    await client.capture_commit(lease, NOOP)
                    order.append(tenant)
            await asyncio.sleep(0)

    sweeping = asyncio.create_task(run_workers(len(workers), sweep))
    try:
        async with asyncio.timeout(30):
            while len(order) < 40:  # the backlog is being worked through
                await asyncio.sleep(0.01)
        for n in range(5):
            await queuer.capture_enqueue(tenant_id="small", session_id="small-s", notebook_id=small, run_ids=[f"r{n}"])
        queued_at = len(order)
        async with asyncio.timeout(30):
            while order.count("small") < 5:
                await asyncio.sleep(0.01)
        served_after = order.index("small", queued_at) - queued_at
        finished_after = len(order) - queued_at
    finally:
        stop.set()
        await sweeping
    # Five small jobs among ~four workers: about half of every round is theirs until done.
    assert served_after <= len(workers), f"its first job waited behind {served_after} of the backlog's"
    assert finished_after <= 5 * 3, f"its five jobs took {finished_after} claims, 9,960 still queued ahead"


async def test_replicas_commit_two_sessions_into_one_notebook_independently(replicas: Replicas) -> None:
    """Grok's ``independent_handles`` test: two handles (here, replicas) on one store each
    queue, claim and commit their own session at once; both land, neither waits on the other."""
    first, second = await replicas.start(2)
    a, b = first.client(), second.client()
    nb = await _notebook(a, "t1")

    async def session(i: int) -> None:
        client, sid = (a, b)[i], f"s{i}"
        for n in range(1, 6):
            await client.capture_enqueue(tenant_id="t1", session_id=sid, notebook_id=nb, run_ids=[f"r{n}"])
        while (lease := await client.capture_claim(tenant_id="t1", owner=f"h{i}", lease_s=60, session_id=sid)) is not None:
            await client.capture_commit(lease, _outcome(f"{sid} turn {lease.job.through_turn}"))

    await asyncio.wait_for(run_workers(2, session), 60)
    await _indexed(a, "t1", ["s0", "s1"], 5)
    facts = await _landed(b, nb, 10)
    assert sorted(f.text for f in facts) == sorted(f"s{i} turn {n}" for i in range(2) for n in range(1, 6))
    for client in (a, b):
        for sid in ("s0", "s1"):
            barrier = await client.capture_flush(tenant_id="t1", session_id=sid, timeout_s=0)
            assert barrier["cursors"] == {"requested": 5, "captured": 5, "indexed": 5}, "both replicas see both"
