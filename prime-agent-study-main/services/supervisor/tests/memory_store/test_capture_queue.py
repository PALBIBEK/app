"""The durable capture queue (G3), ported test for test from Grok's
``xai-grok-memory/src/v2_capture_tests.rs`` where it applies, then ours.

Grok's store keeps observation files, a lexical index and a manifest beside its queue; ours
queues a committed outcome into the notebook's inbox (G2), so "indexed" means its facts
have landed. Grok's tests pass ``now`` to each call; ours set the queue's clock.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio

from supervisor.memory_service.capture.errors import CaptureConflict, StaleLease, StaleResolution
from supervisor.memory_service.capture.queue import (
    MAX_CAPTURE_ATTEMPTS,
    MAX_FAILURE_BYTES,
    CaptureCursors,
    CaptureLease,
    CaptureQueue,
    CaptureWorkState,
)
from supervisor.memory_service.notebooks.types import Sensitivity
from supervisor.memory_service.notebooks.wire import observation_out
from supervisor.memory_service.observations import PROMPT_VERSION, ObservationDraft, ObservationType
from tests.support.notebooks import BACKENDS, MemoryServiceHandle, _database, memory_service

pytestmark = pytest.mark.asyncio

PIN = "composition:githi@sha256:capture"
T = "t1"


class Clock:
    def __init__(self, at: int = 10_000) -> None:
        self.at = at

    def __call__(self) -> int:
        return self.at


@pytest_asyncio.fixture(params=BACKENDS)
async def svc(request: pytest.FixtureRequest, tmp_path: Path) -> AsyncIterator[MemoryServiceHandle]:
    """On SQLite, and on Postgres (its own SQL: locks, grouping, partial indexes)."""
    async with _database(request.param, tmp_path, "memory") as url, memory_service(url) as handle:
        await handle.app.state.memory.stop_background()  # the tests drain and reconcile
        handle.app.state.memory.notebooks.on_queued = lambda: None
        yield handle


@pytest.fixture
def clock(svc: MemoryServiceHandle) -> Clock:
    tick = Clock()
    svc.app.state.memory.capture._clock = tick
    return tick


@pytest_asyncio.fixture
async def notebook(svc: MemoryServiceHandle) -> str:
    return (await svc.client().ensure(tenant_id=T, owner="alice", agent_pin=PIN, name="default")).id


def queue(svc: MemoryServiceHandle) -> CaptureQueue:
    return svc.app.state.memory.capture  # type: ignore[no-any-return]


def runs(start: int, through: int) -> list[str]:
    return [f"run-{i}" for i in range(start, through + 1)]


async def enqueue(svc: MemoryServiceHandle, notebook: str, session: str, start: int, through: int) -> None:
    await queue(svc).enqueue(tenant_id=T, session_id=session, notebook_id=notebook, run_ids=runs(start, through))


async def claim(svc: MemoryServiceHandle, owner: str, lease_s: float = 60, **kw: str) -> CaptureLease:
    lease = await queue(svc).claim(tenant_id=T, owner=owner, lease_s=lease_s, **kw)
    assert lease is not None
    return lease


def observation(statement: str) -> dict[str, object]:
    return observation_out(
        ObservationDraft(
            observation_type=ObservationType.PROJECT,
            topic_hint="capture",
            statement=statement,
            keywords=("durable", "postgres"),
            aliases=("persistence",),
            extraction_model="model-test",
            prompt_version=PROMPT_VERSION,
            created_at=1_788_000_000,
            body="Bounded supporting detail.",
        )
    )


OWN = {"sensitivity": Sensitivity.ORDINARY.value, "inherited": []}
NOOP: dict[str, object] = {"observations": [], "retire": []}


def observations(*statements: str) -> dict[str, object]:
    return {"observations": [observation(s) for s in statements], "retire": [], "context": OWN}


async def cursors(svc: MemoryServiceHandle, session: str = "session") -> CaptureCursors:
    return await queue(svc).cursors(tenant_id=T, session_id=session)


# --- ported from Grok -------------------------------------------------------------------------


async def test_duplicate_enqueue_is_one_job_and_requested_cursor_is_monotonic(svc: MemoryServiceHandle, notebook: str) -> None:
    q = queue(svc)
    first = await q.enqueue(tenant_id=T, session_id="session", notebook_id=notebook, run_ids=runs(1, 4))
    duplicate = await q.enqueue(tenant_id=T, session_id="session", notebook_id=notebook, run_ids=runs(1, 4))
    second = await q.enqueue(tenant_id=T, session_id="session", notebook_id=notebook, run_ids=runs(5, 8))
    again = await q.enqueue(tenant_id=T, session_id="session", notebook_id=notebook, run_ids=runs(1, 4))

    assert first == duplicate == again
    assert (first.from_turn, first.through_turn, second.from_turn, second.through_turn) == (1, 4, 5, 8)
    assert (await cursors(svc)).requested == 8, "a duplicate never moves the requested cursor"


async def test_noop_advances_cursors_without_inbox_or_fact_changes(svc: MemoryServiceHandle, notebook: str, clock: Clock) -> None:
    await enqueue(svc, notebook, "session", 1, 3)

    await queue(svc).commit(await claim(svc, "worker"), NOOP)

    assert await svc.app.state.memory.notebooks.store.pending() == 0
    assert await svc.client().facts(notebook) == []
    assert await cursors(svc) == CaptureCursors(requested=3, captured=3, indexed=3)


async def test_retryable_failure_does_not_advance_any_completed_cursor(svc: MemoryServiceHandle, notebook: str, clock: Clock) -> None:
    q = queue(svc)
    await enqueue(svc, notebook, "session", 1, 3)
    await q.fail_retryable(await claim(svc, "first-worker"), "malformed structured output")

    assert await q.work_state(tenant_id=T, session_id="session", through_turn=3) == CaptureWorkState(
        failed=1, last_error="malformed structured output"
    )
    assert await cursors(svc) == CaptureCursors(requested=3, captured=0, indexed=0)
    clock.at += 60_000  # past its backoff
    retried = await claim(svc, "retry-worker", session_id="session")
    assert retried.job.attempt == 2
    await q.commit(retried, NOOP)
    assert (await cursors(svc)).indexed == 3


async def test_pending_work_is_claimed_before_failed_work_is_retried(svc: MemoryServiceHandle, notebook: str, clock: Clock) -> None:
    q = queue(svc)
    await enqueue(svc, notebook, "session", 1, 1)
    await q.fail_retryable(await claim(svc, "first-worker"), "malformed structured output")
    await enqueue(svc, notebook, "session", 2, 2)
    clock.at += 60_000  # both claimable

    pending = await claim(svc, "pending-worker")
    assert (pending.job.from_turn, pending.job.attempt) == (2, 1)
    await q.commit(pending, NOOP)

    retried = await claim(svc, "retry-worker")
    assert (retried.job.from_turn, retried.job.attempt) == (1, 2)


async def test_retryable_failure_text_cannot_strand_a_running_lease(svc: MemoryServiceHandle, notebook: str, clock: Clock) -> None:
    q = queue(svc)
    await enqueue(svc, notebook, "session", 1, 1)
    await q.fail_retryable(await claim(svc, "first-worker"), "\n" + "é" * MAX_FAILURE_BYTES + "\0")

    state = await q.work_state(tenant_id=T, session_id="session", through_turn=1)
    assert (state.running, state.failed) == (0, 1)
    assert state.last_error and len(state.last_error.encode()) <= MAX_FAILURE_BYTES
    assert not any(ord(c) < 0x20 for c in state.last_error)
    clock.at += 60_000
    assert (await claim(svc, "retry-worker")).job.attempt == 2


async def test_cancellation_release_makes_lease_immediately_retryable(svc: MemoryServiceHandle, notebook: str, clock: Clock) -> None:
    q = queue(svc)
    await enqueue(svc, notebook, "session", 1, 1)
    interrupted = await claim(svc, "first-worker")

    await q.release_retryable(interrupted, "worker interrupted")
    retried = await claim(svc, "replacement-worker")  # at the same instant

    assert retried.job.attempt == 2
    assert (await q.work_state(tenant_id=T, session_id="session", through_turn=1)).running == 1
    with pytest.raises(StaleLease):
        await q.release_retryable(interrupted, "stale cleanup")


async def test_retry_same_bytes_is_idempotent_and_different_outcome_is_rejected(svc: MemoryServiceHandle, notebook: str, clock: Clock) -> None:
    q = queue(svc)
    await enqueue(svc, notebook, "session", 1, 2)
    lease = await claim(svc, "worker")
    outcome = observations("Retry-safe immutable fact")

    first = await q.commit(lease, outcome)
    second = await q.commit(lease, outcome)

    assert first.outcome_hash == second.outcome_hash and second.was_already_committed
    with pytest.raises(CaptureConflict):
        await q.commit(lease, observations("Changed retry bytes"))
    assert await svc.app.state.memory.notebooks.store.pending() == 1, "queued once"


async def test_expired_lease_is_reclaimed_and_stale_owner_is_fenced(svc: MemoryServiceHandle, notebook: str, clock: Clock) -> None:
    q = queue(svc)
    await enqueue(svc, notebook, "session", 1, 2)
    old = await claim(svc, "old", lease_s=5)
    clock.at += 5_000
    new = await claim(svc, "new", lease_s=5)

    assert new.job.attempt == old.job.attempt + 1
    with pytest.raises(StaleLease):
        await q.commit(old, NOOP)
    with pytest.raises(StaleLease):
        await q.fail_retryable(old, "late")
    await q.commit(new, NOOP)


@pytest.mark.parametrize("diagnostic", ["", "x" * (MAX_FAILURE_BYTES + 1)])
async def test_terminal_failure_sanitizes_diagnostic_before_transition(
    svc: MemoryServiceHandle, notebook: str, clock: Clock, diagnostic: str
) -> None:
    q = queue(svc)
    await enqueue(svc, notebook, "terminal", 1, 1)
    await q.fail_terminal(await claim(svc, "worker"), diagnostic)

    state = await q.work_state(tenant_id=T, session_id="terminal", through_turn=1)
    assert state.last_error and len(state.last_error.encode()) <= MAX_FAILURE_BYTES
    clock.at += 3_600_000
    assert await q.claim(tenant_id=T, owner="retry", lease_s=60, session_id="terminal") is None, "never claimable again"
    assert (await cursors(svc, "terminal")).captured == 1, "a terminal failure resolves its range"


async def test_completed_job_retries_reconciliation_without_reextracting(
    svc: MemoryServiceHandle, notebook: str, clock: Clock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The outcome is committed before it is queued: a crash (here, the inbox refusing for
    a while) between the two leaves it captured, not indexed, and not claimable again;
    reconciliation queues the kept outcome."""
    memory = svc.app.state.memory
    q = queue(svc)
    await enqueue(svc, notebook, "session", 1, 2)
    lease = await claim(svc, "worker")
    observe = memory.notebooks.observe

    async def down(*args: object, **kw: object) -> int:
        raise OSError("the database went away")

    monkeypatch.setattr(memory.notebooks, "observe", down)
    await q.commit(lease, observations("Reconcile retry token"))

    assert await cursors(svc) == CaptureCursors(requested=2, captured=2, indexed=0)
    assert await q.claim(tenant_id=T, owner="must-not-reextract", lease_s=60, session_id="session") is None

    monkeypatch.setattr(memory.notebooks, "observe", observe)
    clock.at += 10_000  # past the committing call's grace
    await q.reconcile()
    await memory.inbox.drain()
    assert [f.text for f in await svc.client().facts(notebook)] == ["Reconcile retry token"]
    assert (await cursors(svc)).indexed == 2


# --- ours -------------------------------------------------------------------------------------


async def test_committed_observations_land_and_then_the_session_is_indexed(svc: MemoryServiceHandle, notebook: str, clock: Clock) -> None:
    memory = svc.app.state.memory
    await enqueue(svc, notebook, "session", 1, 2)
    await queue(svc).commit(await claim(svc, "worker"), observations("Builds with make check"))

    assert await cursors(svc) == CaptureCursors(requested=2, captured=2, indexed=0), "queued, not landed"
    await memory.inbox.drain()

    (fact,) = await svc.client().facts(notebook)
    assert fact.text == "Builds with make check" and fact.typed is not None
    assert (await cursors(svc)).indexed == 2


async def test_a_cursor_never_moves_past_a_turn_still_pending(svc: MemoryServiceHandle, notebook: str, clock: Clock) -> None:
    q = queue(svc)
    await enqueue(svc, notebook, "session", 1, 2)
    await enqueue(svc, notebook, "session", 3, 4)
    first = await claim(svc, "a")
    second = await claim(svc, "b")
    assert (first.job.from_turn, second.job.from_turn) == (1, 3)

    await q.commit(second, NOOP)
    assert await cursors(svc) == CaptureCursors(requested=4, captured=0, indexed=0)
    await q.commit(first, NOOP)
    assert await cursors(svc) == CaptureCursors(requested=4, captured=4, indexed=4)


async def test_a_job_failing_every_claim_is_terminal_at_its_last(svc: MemoryServiceHandle, notebook: str, clock: Clock) -> None:
    q = queue(svc)
    await enqueue(svc, notebook, "session", 1, 1)
    for _ in range(MAX_CAPTURE_ATTEMPTS):
        await q.fail_retryable(await claim(svc, "worker"), "the model is down")
        clock.at += 600_000

    assert await q.claim(tenant_id=T, owner="worker", lease_s=60) is None
    assert (await cursors(svc)).captured == 1


async def test_a_resolution_against_a_changed_notebook_is_refused_and_resolved_again(
    svc: MemoryServiceHandle, notebook: str, clock: Clock
) -> None:
    """S3: the verdict was made against a revision; another session's capture queued (or
    landed) since makes it stale. The lease is kept, so the worker commits once resolved."""
    q = queue(svc)
    await enqueue(svc, notebook, "a", 1, 1)
    await enqueue(svc, notebook, "b", 1, 1)
    a, b = await claim(svc, "wa", session_id="a"), await claim(svc, "wb", session_id="b")
    seen = await q.revision(notebook)

    await q.commit(a, observations("Deploys on Fridays"), resolved_at=seen)
    with pytest.raises(StaleResolution):
        await q.commit(b, observations("Deploys on Friday"), resolved_at=seen)
    await q.commit(b, observations("Deploys on Friday"), resolved_at=await q.revision(notebook))


async def test_a_commit_changes_the_revision_before_its_facts_are_queued(
    svc: MemoryServiceHandle, notebook: str, clock: Clock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """S3, between commit and queuing (the queue down, or a crash): the commit itself counts,
    so a second capture resolved against the earlier revision is still refused."""
    q = queue(svc)
    await enqueue(svc, notebook, "a", 1, 1)
    await enqueue(svc, notebook, "b", 1, 1)
    a, b = await claim(svc, "wa", session_id="a"), await claim(svc, "wb", session_id="b")
    seen = await q.revision(notebook)

    async def down(*args: object, **kwargs: object) -> int:
        raise ConnectionError("the inbox is unreachable")

    monkeypatch.setattr(svc.app.state.memory.notebooks, "observe", down)
    await q.commit(a, observations("Deploys on Fridays"), resolved_at=seen)
    assert await svc.app.state.memory.notebooks.store.pending() == 0, "committed, not queued"
    with pytest.raises(StaleResolution):
        await q.commit(b, observations("Deploys on Friday"), resolved_at=seen)
    await q.commit(b, NOOP, resolved_at=seen)  # nothing to change: never stale


async def test_an_erased_owners_captures_are_scrubbed_and_their_sessions_resolved(
    svc: MemoryServiceHandle, notebook: str, clock: Clock
) -> None:
    from sqlalchemy import select

    from supervisor.memory_service.capture.models import CaptureJobRow

    memory = svc.app.state.memory
    q = queue(svc)
    await enqueue(svc, notebook, "session", 1, 1)
    await enqueue(svc, notebook, "session", 2, 2)
    committed = await claim(svc, "worker")
    await q.commit(committed, observations("Lives at an address"))  # queued, not landed (no drainer)
    held = await claim(svc, "worker")  # turn 2, running

    erased = await memory.erase_subject(tenant=T, subject="user:alice", actor="ops", reason="request")

    assert erased["notebooks_erased"] == 1 and erased["captures_scrubbed"] == 2
    async with memory._factory() as db:
        assert [r.outcome for r in await db.scalars(select(CaptureJobRow))] == [None, None], "nothing of it kept"
    assert await cursors(svc) == CaptureCursors(requested=2, captured=2, indexed=2), "no cursor stalls on them"
    with pytest.raises(StaleLease):
        await q.commit(held, observations("Late"))


async def test_the_barrier_waits_for_the_session_to_be_indexed(svc: MemoryServiceHandle, notebook: str, clock: Clock) -> None:
    import asyncio

    memory = svc.app.state.memory
    q = queue(svc)
    await enqueue(svc, notebook, "session", 1, 2)
    result, _, _ = await q.flush(tenant_id=T, session_id="session", through_turn=None, timeout_s=0.05)
    assert result == "timeout"

    waiting = asyncio.create_task(q.flush(tenant_id=T, session_id="session", through_turn=None, timeout_s=10, poll_s=0.01))
    await q.commit(await claim(svc, "worker"), observations("Lands, then the barrier lifts"))
    await asyncio.sleep(0.05)
    assert not waiting.done(), "captured is not indexed"
    await memory.inbox.drain()
    result, cursors_now, _ = await waiting
    assert result == "success" and cursors_now.indexed == 2


async def test_the_barrier_says_a_retry_is_pending_or_a_range_failed(svc: MemoryServiceHandle, notebook: str, clock: Clock) -> None:
    q = queue(svc)
    await enqueue(svc, notebook, "session", 1, 1)
    await q.fail_retryable(await claim(svc, "worker"), "the model is down")
    assert (await q.flush(tenant_id=T, session_id="session", through_turn=1, timeout_s=0))[0] == "retry"
    clock.at += 60_000
    await q.fail_terminal(await claim(svc, "worker"), "refused for good")
    assert (await q.flush(tenant_id=T, session_id="session", through_turn=1, timeout_s=0))[0] == "failed"


async def test_tenants_with_work_are_ordered_by_their_running_jobs(svc: MemoryServiceHandle, clock: Clock) -> None:
    client = svc.client()
    q = queue(svc)
    for tenant in ("busy", "quiet"):
        nb = await client.ensure(tenant_id=tenant, owner="alice", agent_pin=PIN, name="default")
        for s in range(3):
            await q.enqueue(tenant_id=tenant, session_id=f"{tenant}-{s}", notebook_id=nb.id, run_ids=["r"])
    clock.at += 1
    await q.claim(tenant_id="busy", owner="w", lease_s=60)
    await q.claim(tenant_id="busy", owner="w", lease_s=60)

    assert await q.claimable_tenants() == ["quiet", "busy"], "the tenant running less goes first"


async def test_a_job_whose_inbox_row_landed_or_vanished_is_indexed_by_reconciliation(
    svc: MemoryServiceHandle, notebook: str, clock: Clock
) -> None:
    """Told late (the drainer's tell lost): reconciliation finds the inbox row handled."""
    memory = svc.app.state.memory
    memory.notebooks.on_landed = None
    q = queue(svc)
    await enqueue(svc, notebook, "session", 1, 1)
    await q.commit(await claim(svc, "worker"), observations("Landed while no one was told"))
    await memory.inbox.drain()
    assert (await cursors(svc)).indexed == 0

    await q.reconcile()
    assert (await cursors(svc)).indexed == 1


async def test_a_job_whose_notebook_was_archived_resolves_and_never_stalls_the_cursor(
    svc: MemoryServiceHandle, notebook: str, clock: Clock
) -> None:
    q = queue(svc)
    await enqueue(svc, notebook, "session", 1, 1)
    lease = await claim(svc, "worker")
    await svc.app.state.memory.notebooks.store.set_archived(notebook, True)

    await q.commit(lease, observations("Into an archived notebook"))

    assert await cursors(svc) == CaptureCursors(requested=1, captured=1, indexed=1)
    assert (await q.flush(tenant_id=T, session_id="session", through_turn=1, timeout_s=0))[0] == "failed"
    assert (await q.work_state(tenant_id=T, session_id="session", through_turn=1)).terminal == 1


async def test_another_tenants_session_is_not_found(svc: MemoryServiceHandle, notebook: str, clock: Clock) -> None:
    from supervisor.memory_service.capture.errors import UnknownCaptureSession

    await enqueue(svc, notebook, "session", 1, 1)
    with pytest.raises(UnknownCaptureSession):
        await queue(svc).cursors(tenant_id="t2", session_id="session")
    assert await queue(svc).claim(tenant_id="t2", owner="w", lease_s=60) is None


async def test_resolved_jobs_are_pruned_and_pending_ones_kept(svc: MemoryServiceHandle, notebook: str, clock: Clock) -> None:
    q = queue(svc)
    await enqueue(svc, notebook, "session", 1, 1)
    await enqueue(svc, notebook, "session", 2, 2)
    await q.commit(await claim(svc, "worker"), NOOP)

    assert await q.prune(before=clock.at + 1) == 1
    assert (await q.work_state(tenant_id=T, session_id="session", through_turn=2)).pending == 1
