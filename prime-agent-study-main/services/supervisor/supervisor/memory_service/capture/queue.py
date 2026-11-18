"""The durable capture queue (G3; ``docs/memory-gaps.md``, gap S4).

Ported from Grok's ``V2CaptureStore`` (``xai-grok-memory/src/v2_capture.rs``) for intent, on
the memory service's Postgres instead of a SQLite file per scope:

- A burst of a session's turns is one job, keyed by the session and its run ids, so an
  enqueue retried by Temporal is the same job. Its turn range is given at its first enqueue,
  after the session's last: ranges never overlap, and the ``requested`` cursor only grows.
- A claim leases a job (``FOR UPDATE SKIP LOCKED``, so any number of workers drain the queue
  without coordinating). The lease carries the claim's ``attempt``, its generation: commit,
  fail and release all name it, so a worker whose lease expired and was claimed again is
  fenced out (``StaleLease``), not merely late.
- Commit records the outcome and its hash in one transaction with the lease's check: the
  same outcome again is idempotent, another is refused (``CaptureConflict``). The outcome is
  then queued into the notebook's inbox (G2) under the job's id. A crash between the two is
  repaired by ``reconcile`` from the kept outcome, never by extracting again.
- Cursors, per session: ``captured`` covers the turns whose outcome is committed (or failed
  for good), ``indexed`` those whose facts have landed. Each advances over a contiguous run
  of resolved jobs only, so neither moves past a turn still pending.
- A retryable failure backs off; a terminal one resolves its range, so the cursors never
  stall on it. Failure text is diagnostic, sanitized and bounded before it is kept.

Ours, beyond Grok's: claims are fair across tenants (``claimable_tenants``: the tenants
with the fewest running jobs first), and a commit can name the notebook revision its
resolution read (S3): if the notebook changed since, it is refused (``StaleResolution``)
and the worker resolves again.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, Final, Literal

from sqlalchemy import case, delete, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from supervisor.memory_service.capture.errors import (
    CaptureConflict,
    CaptureInvalid,
    StaleLease,
    StaleResolution,
    UnknownCaptureSession,
)
from supervisor.memory_service.capture.models import CaptureJobRow, CaptureSessionRow
from supervisor.memory_service.notebooks.errors import (
    InvalidObservation,
    MemoryLimitExceeded,
    NotebookArchived,
    NotebookNotFound,
)
from supervisor.memory_service.notebooks.models import InboxRow, NotebookRow
from supervisor.memory_service.notebooks.ops import PROMOTE_ATTEMPTS, NotebookOps
from supervisor.memory_service.notebooks.wire import context_in, observation_in
from supervisor.memory_service.observations import ObservationInvalid

__all__ = [
    "CaptureCursors",
    "CaptureJob",
    "CaptureLease",
    "CaptureQueue",
    "CaptureWorkState",
    "CommitResult",
    "job_id_for",
    "sanitize_failure",
]

logger = logging.getLogger(__name__)

MAX_RUNS_PER_JOB: Final = 200
MAX_TURN: Final = 999_999  # Grok's six-digit durable bound
MAX_OWNER_BYTES: Final = 128
MAX_SESSION_BYTES: Final = 256
MAX_FAILURE_BYTES: Final = 512
MAX_LEASE_S: Final = 3_600
MAX_CAPTURE_ATTEMPTS: Final = 8
"""Claims a job gets before a retryable failure is taken as terminal: ours, beyond Grok's
(whose single-user queue retries for ever)."""
RETRY_BACKOFF_MS: Final = 5_000
MAX_RETRY_BACKOFF_MS: Final = 300_000
"""A failed job is claimable again after 5 s, doubling to 5 minutes."""
LAND_GRACE_MS: Final = 2_000
"""How long after its commit a job's outcome is left to the committing call to queue, before
``reconcile`` queues it (a crash between the two)."""
KEEP_JOBS_MS: Final = 7 * 24 * 3600 * 1000
"""Resolved jobs are kept a week (a retried enqueue still finds its job), then pruned."""
RECONCILE_BATCH: Final = 200

Status = Literal["pending", "running", "completed", "failed"]
FlushResult = Literal["success", "retry", "failed", "timeout"]


def now_ms() -> int:
    return int(time.time() * 1000)


def job_id_for(session_id: str, run_ids: Sequence[str]) -> str:
    """A burst's job id: the same session and runs are the same job, whoever enqueues it."""
    digest = hashlib.sha256("\0".join([session_id, *run_ids]).encode()).hexdigest()
    return f"cap-{digest[:40]}"


def sanitize_failure(error: str) -> str:
    """Grok's ``sanitize_failure``: control characters as spaces, trimmed, never empty, at
    most ``MAX_FAILURE_BYTES`` of UTF-8 cut on a character."""
    normalized = _CONTROLS.sub(" ", error).strip() or "capture operation failed"
    cut = normalized.encode("utf-8", "replace")[:MAX_FAILURE_BYTES].decode("utf-8", "ignore")
    return cut.rstrip() or "capture operation failed"


_CONTROLS: Final = re.compile("[\x00-\x1f\x7f-\x9f]")


@dataclass(frozen=True, slots=True)
class CaptureJob:
    job_id: str
    tenant_id: str
    session_id: str
    notebook_id: str
    run_ids: tuple[str, ...]
    from_turn: int
    through_turn: int
    attempt: int


@dataclass(frozen=True, slots=True)
class CaptureLease:
    job: CaptureJob
    owner: str
    expires_at: int  # epoch ms


@dataclass(frozen=True, slots=True)
class CaptureCursors:
    requested: int = 0
    captured: int = 0
    indexed: int = 0


@dataclass(frozen=True, slots=True)
class CaptureWorkState:
    """Content-free job state through a turn (Grok's ``work_state``)."""

    pending: int = 0
    running: int = 0
    failed: int = 0  # retryable: claimable again once their backoff passes
    terminal: int = 0
    last_error: str | None = None


@dataclass(frozen=True, slots=True)
class CommitResult:
    outcome_hash: str
    was_already_committed: bool


def outcome_hash(outcome: dict[str, Any]) -> str:
    """The outcome's identity: the same observations, retirements and context, byte for
    byte once canonical."""
    canonical = json.dumps(outcome, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8", "surrogatepass")).hexdigest()


def is_noop(outcome: dict[str, Any]) -> bool:
    return not outcome.get("observations") and not outcome.get("retire")


class CaptureQueue:
    def __init__(
        self,
        factory: async_sessionmaker[AsyncSession],
        notebooks: NotebookOps,
        *,
        clock: Callable[[], int] = now_ms,
    ) -> None:
        self._db = factory
        self._notebooks = notebooks
        self._clock = clock

    # --- enqueue ---------------------------------------------------------------------------

    async def enqueue(
        self, *, tenant_id: str, session_id: str, notebook_id: str, run_ids: Sequence[str]
    ) -> CaptureJob:
        """Queue a burst: its turns follow the session's last queued one. The same burst
        again is the same job, with the range it was first given."""
        _within_bytes("session", session_id, MAX_SESSION_BYTES)
        if not run_ids or len(run_ids) > MAX_RUNS_PER_JOB or len(set(run_ids)) != len(run_ids):
            raise CaptureInvalid(f"a capture burst names 1..={MAX_RUNS_PER_JOB} distinct runs")
        job_id = job_id_for(session_id, run_ids)
        now = self._clock()
        async with self._db() as db:
            session = await self._session_for_update(db, tenant_id, session_id, now)
            # After the session's lock: a burst enqueued twice at once is found, not repeated.
            if (row := await db.get(CaptureJobRow, job_id)) is not None:
                if row.session_id != session_id or row.tenant_id != tenant_id:
                    raise CaptureConflict("capture job id names another session's burst")
                await db.commit()
                return _job(row)
            start = int(session.requested) + 1
            through = start + len(run_ids) - 1
            if through > MAX_TURN:
                raise CaptureInvalid("capture turn exceeds the six-digit durable limit")
            row = CaptureJobRow(
                job_id=job_id,
                tenant_id=tenant_id,
                session_id=session_id,
                notebook_id=notebook_id,
                run_ids=list(run_ids),
                from_turn=start,
                through_turn=through,
                status="pending",
                terminal=False,
                attempt=0,
                available_at=now,
                queued=False,
                indexed=False,
                created_at=now,
                updated_at=now,
            )
            db.add(row)
            session.requested = through
            session.updated_at = now
            await db.commit()
            return _job(row)

    async def _session_for_update(
        self, db: AsyncSession, tenant_id: str, session_id: str, now: int
    ) -> CaptureSessionRow:
        query = select(CaptureSessionRow).where(CaptureSessionRow.session_id == session_id).with_for_update()
        if (row := await db.scalar(query)) is None:
            try:
                async with db.begin_nested():
                    db.add(CaptureSessionRow(session_id=session_id, tenant_id=tenant_id, updated_at=now))
            except IntegrityError:
                pass  # created by a racing enqueue: it is read, locked, below
            row = await db.scalar(query)
        assert row is not None
        if row.tenant_id != tenant_id:
            raise UnknownCaptureSession(session_id)  # another tenant's: as if absent
        return row

    # --- claims ----------------------------------------------------------------------------

    async def claimable_tenants(self, *, limit: int = 32, pending_grace_ms: int = 0) -> list[str]:
        """The tenants with work to claim, the fewest running jobs first, then the longest
        waiting: a worker takes its next job from the first, so one tenant's backlog never
        holds up another's (rule 6). Content-free."""
        now = self._clock()
        async with self._db() as db:
            waiting = dict(
                (await db.execute(
                    select(CaptureJobRow.tenant_id, func.min(CaptureJobRow.available_at))
                    .where(_claimable(now, pending_grace_ms))
                    .group_by(CaptureJobRow.tenant_id)
                )).all()
            )
            running = dict(
                (await db.execute(
                    select(CaptureJobRow.tenant_id, func.count())
                    .where(CaptureJobRow.status == "running", CaptureJobRow.lease_expires_at > now)
                    .group_by(CaptureJobRow.tenant_id)
                )).all()
            )
        order = sorted(waiting, key=lambda t: (running.get(t, 0), waiting[t], t))
        return order[:limit]

    async def claim(
        self,
        *,
        tenant_id: str,
        owner: str,
        lease_s: float,
        session_id: str | None = None,
        job_id: str | None = None,
        pending_grace_ms: int = 0,
    ) -> CaptureLease | None:
        """Lease the tenant's next job (or the session's, or one job): pending work before
        failed work being retried (Grok's order), oldest first; a running job whose lease
        expired is claimable again. None when there is none. ``pending_grace_ms``: a pending
        job younger than this is left to whoever queued it (the sweeper's claims)."""
        _within_bytes("lease owner", owner, MAX_OWNER_BYTES)
        if not 1 <= lease_s <= MAX_LEASE_S:
            raise CaptureInvalid(f"a lease lasts 1..={MAX_LEASE_S} seconds")
        now = self._clock()
        expires = now + int(lease_s * 1000)
        query = select(CaptureJobRow).where(CaptureJobRow.tenant_id == tenant_id, _claimable(now, pending_grace_ms))
        if session_id is not None:
            query = query.where(CaptureJobRow.session_id == session_id)
        if job_id is not None:
            query = query.where(CaptureJobRow.job_id == job_id)
        query = (
            query.order_by(
                (CaptureJobRow.status != "pending"), CaptureJobRow.available_at, CaptureJobRow.from_turn
            )
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        async with self._db() as db:
            if (row := await db.scalar(query)) is None:
                await db.commit()
                return None
            row.status = "running"
            row.lease_owner = owner
            row.lease_expires_at = expires
            row.attempt = int(row.attempt) + 1
            row.last_error = None
            row.updated_at = now
            await db.commit()
            return CaptureLease(_job(row), owner, expires)

    # --- outcomes --------------------------------------------------------------------------

    async def commit(
        self, lease: CaptureLease, outcome: dict[str, Any], *, resolved_at: str | None = None
    ) -> CommitResult:
        """Commit the lease's outcome: the observations to queue (wire form), the fact ids
        they retire, their capture context and author; none of either is a no-op, which
        resolves the range at once. ``resolved_at``: the notebook revision the resolution
        read (``revision``); a notebook changed since refuses it (``StaleResolution``)."""
        outcome = _canonical(outcome)
        digest = outcome_hash(outcome)
        now = self._clock()
        noop = is_noop(outcome)
        async with self._db() as db:
            row = await db.get(CaptureJobRow, lease.job.job_id, with_for_update=True)
            if row is None:
                raise StaleLease()
            if row.outcome_hash is not None:
                await db.commit()
                if row.outcome_hash != digest:
                    raise CaptureConflict("job was already committed with a different outcome")
                return CommitResult(digest, True)
            _fenced(row, lease, now)
            if resolved_at is not None and not noop and (actual := await _revision(db, row.notebook_id)) != resolved_at:
                raise StaleResolution(resolved_at, actual)
            row.status = "completed"
            row.outcome = outcome
            row.outcome_hash = digest
            row.completed_at = now
            row.lease_owner = row.lease_expires_at = None
            row.queued = row.indexed = noop
            row.updated_at = now
            await _advance(db, row.session_id, now)
            await db.commit()
            job = _job(row)
        if not noop:
            await self._land(job, outcome)
        return CommitResult(digest, False)

    async def fail_retryable(self, lease: CaptureLease, error: str) -> None:
        """Return a valid lease's job to retryable work, backing off; its last claim's
        failure is terminal (``MAX_CAPTURE_ATTEMPTS``)."""
        await self._fail(lease, error, terminal=None)

    async def fail_terminal(self, lease: CaptureLease, error: str) -> None:
        """Fail a valid lease's job for good: its range is resolved, so no cursor stalls."""
        await self._fail(lease, error, terminal=True)

    async def _fail(self, lease: CaptureLease, error: str, *, terminal: bool | None) -> None:
        error = sanitize_failure(error)
        now = self._clock()
        async with self._db() as db:
            row = await db.get(CaptureJobRow, lease.job.job_id, with_for_update=True)
            if row is None:
                raise StaleLease()
            _fenced(row, lease, now)
            final = terminal or int(row.attempt) >= MAX_CAPTURE_ATTEMPTS
            row.status, row.terminal, row.last_error = "failed", final, error
            row.lease_owner = row.lease_expires_at = None
            row.available_at = now + min(MAX_RETRY_BACKOFF_MS, RETRY_BACKOFF_MS * 2 ** (int(row.attempt) - 1))
            row.updated_at = now
            if final:
                await _advance(db, row.session_id, now)
            await db.commit()

    async def release_retryable(self, lease: CaptureLease, error: str) -> None:
        """Give an exact owner and attempt's lease back during cancellation: claimable again at
        once, without waiting for its deadline (Grok's ``release_retryable``)."""
        now = self._clock()
        async with self._db() as db:
            released = await db.execute(
                update(CaptureJobRow)
                .where(
                    CaptureJobRow.job_id == lease.job.job_id,
                    CaptureJobRow.session_id == lease.job.session_id,
                    CaptureJobRow.status == "running",
                    CaptureJobRow.lease_owner == lease.owner,
                    CaptureJobRow.attempt == lease.job.attempt,
                )
                .values(
                    status="failed", lease_owner=None, lease_expires_at=None, available_at=now,
                    last_error=sanitize_failure(error), updated_at=now,
                )
            )
            if released.rowcount != 1:  # type: ignore[attr-defined]
                raise StaleLease()
            await db.commit()

    async def _land(self, job: CaptureJob, outcome: dict[str, Any]) -> None:
        """Queue a committed outcome into the notebook's inbox under the job's id (idempotent:
        a second time finds it queued). A refusal that will never pass resolves the job."""
        try:
            await self._notebooks.observe(
                job.notebook_id,
                [observation_in(o) for o in outcome["observations"]],
                author=outcome.get("author"),
                context=context_in(outcome.get("context")),
                retire=list(outcome.get("retire") or ()),
                idempotency_key=job.job_id,
            )
        except (ObservationInvalid, InvalidObservation, MemoryLimitExceeded, NotebookArchived, NotebookNotFound) as exc:
            logger.warning("capture job %s: its outcome cannot be kept: %s", job.job_id, exc)
            await self._resolve_unkept(job.job_id, sanitize_failure(f"{type(exc).__name__}: {exc}"))
            return
        except Exception:  # the database, the embedder: ``reconcile`` queues it again
            logger.warning("capture job %s: queuing its outcome failed", job.job_id, exc_info=True)
            return
        await self._mark(job.job_id, queued=True)

    async def _resolve_unkept(self, job_id: str, error: str) -> None:
        now = self._clock()
        async with self._db() as db:
            row = await db.get(CaptureJobRow, job_id, with_for_update=True)
            if row is None or row.indexed:
                return
            row.status, row.terminal, row.last_error = "failed", True, error
            row.queued = row.indexed = True
            row.updated_at = now
            await _advance(db, row.session_id, now)
            await db.commit()

    async def _mark(self, job_id: str, *, queued: bool) -> None:
        async with self._db() as db:
            await db.execute(
                update(CaptureJobRow).where(CaptureJobRow.job_id == job_id).values(queued=queued, updated_at=self._clock())
            )
            await db.commit()

    # --- landing (G2's inbox) ----------------------------------------------------------------

    async def mark_indexed(self, job_ids: Sequence[str]) -> int:
        """These jobs' facts have landed (the inbox drainer promoted their rows): advance their
        sessions' ``indexed`` cursors."""
        ids = [j for j in job_ids if j.startswith("cap-")]
        if not ids:
            return 0
        now = self._clock()
        async with self._db() as db:
            sessions = list(
                await db.scalars(
                    select(CaptureJobRow.session_id).where(
                        CaptureJobRow.job_id.in_(ids), CaptureJobRow.status == "completed", ~CaptureJobRow.indexed
                    )
                )
            )
            if not sessions:
                return 0
            await db.execute(
                update(CaptureJobRow)
                .where(CaptureJobRow.job_id.in_(ids), CaptureJobRow.status == "completed")
                .values(queued=True, indexed=True, updated_at=now)
            )
            for session_id in sorted(set(sessions)):  # one order: two drainers never deadlock
                await _advance(db, session_id, now)
            await db.commit()
            return len(sessions)

    async def reconcile(self) -> int:
        """Repair every crash window, on any replica, from what the database holds:
        - a committed outcome not yet queued (a crash after the commit) is queued from the
          kept outcome, never extracted again;
        - a queued one whose inbox row has been handled since (landed, or dropped with its
          notebook) is indexed; one set aside in the inbox stays unindexed until requeued.
        The jobs repaired."""
        now = self._clock()
        async with self._db() as db:
            unqueued = list(
                await db.scalars(
                    select(CaptureJobRow)
                    .where(
                        CaptureJobRow.status == "completed",
                        ~CaptureJobRow.indexed,
                        ~CaptureJobRow.queued,
                        CaptureJobRow.completed_at < now - LAND_GRACE_MS,
                    )
                    .order_by(CaptureJobRow.completed_at)
                    .limit(RECONCILE_BATCH)
                )
            )
            queued = list(
                (await db.execute(
                    select(CaptureJobRow.job_id, CaptureJobRow.notebook_id, InboxRow.promoted_at, InboxRow.attempts)
                    .outerjoin(
                        InboxRow,
                        (InboxRow.notebook_id == CaptureJobRow.notebook_id)
                        & (InboxRow.idempotency_key == CaptureJobRow.job_id),
                    )
                    .where(CaptureJobRow.status == "completed", ~CaptureJobRow.indexed, CaptureJobRow.queued)
                    .limit(RECONCILE_BATCH)
                )).all()
            )
        repaired = 0
        for row in unqueued:
            await self._land(_job(row), row.outcome or {})
            repaired += 1
        landed = [
            job_id
            for job_id, _notebook, promoted_at, attempts in queued
            # Handled (landed or dropped), or gone (pruned, erased): never coming back. One set
            # aside (attempts at the limit) is not landed: requeued, it lands then.
            if promoted_at is None and attempts is None or (promoted_at is not None and int(attempts or 0) < PROMOTE_ATTEMPTS)
        ]
        return repaired + await self.mark_indexed(landed)

    # --- reads -----------------------------------------------------------------------------

    async def cursors(self, *, tenant_id: str, session_id: str) -> CaptureCursors:
        async with self._db() as db:
            row = await db.get(CaptureSessionRow, session_id)
            if row is None or row.tenant_id != tenant_id:
                raise UnknownCaptureSession(session_id)
            return CaptureCursors(int(row.requested), int(row.captured), int(row.indexed))

    async def work_state(self, *, tenant_id: str, session_id: str, through_turn: int) -> CaptureWorkState:
        """Content-free job state through ``through_turn`` (Grok's ``work_state``)."""
        in_range = (
            CaptureJobRow.tenant_id == tenant_id,
            CaptureJobRow.session_id == session_id,
            CaptureJobRow.from_turn <= through_turn,
        )
        async with self._db() as db:
            counts: dict[str, int] = dict(
                (await db.execute(
                    select(_state_of(), func.count()).where(*in_range).group_by(_state_of())
                )).all()
            )
            last_error = await db.scalar(
                select(CaptureJobRow.last_error)
                .where(*in_range, CaptureJobRow.status == "failed")
                .order_by(CaptureJobRow.updated_at.desc())
                .limit(1)
            )
        return CaptureWorkState(
            pending=counts.get("pending", 0),
            running=counts.get("running", 0),
            failed=counts.get("failed", 0),
            terminal=counts.get("terminal", 0),
            last_error=last_error,
        )

    async def flush(
        self, *, tenant_id: str, session_id: str, through_turn: int | None, timeout_s: float, poll_s: float = 0.2
    ) -> tuple[FlushResult, CaptureCursors, CaptureWorkState]:
        """Grok's ``/flush`` barrier: wait until the session's capture is indexed through
        ``through_turn`` (its latest requested turn when None), or ``timeout_s``. It waits on
        this session's cursor only, never on the queue."""
        deadline = time.monotonic() + max(0.0, timeout_s)
        cursors = await self.cursors(tenant_id=tenant_id, session_id=session_id)
        target = cursors.requested if through_turn is None else through_turn
        while cursors.indexed < target and time.monotonic() < deadline:
            await asyncio.sleep(min(poll_s, max(0.0, deadline - time.monotonic())))
            cursors = await self.cursors(tenant_id=tenant_id, session_id=session_id)
        state = await self.work_state(tenant_id=tenant_id, session_id=session_id, through_turn=target)
        if cursors.indexed >= target:
            return ("failed" if state.terminal else "success"), cursors, state
        return ("retry" if state.failed else "timeout"), cursors, state

    async def revision(self, notebook_id: str) -> str:
        """The notebook's revision as resolution sees it (S3): its facts' revision and its
        queue's newest capture, so either landing or queuing changes it."""
        async with self._db() as db:
            return await _revision(db, notebook_id)

    async def prune(self, *, before: int | None = None) -> int:
        """Delete resolved jobs last changed before ``before`` (a week ago by default)."""
        before = self._clock() - KEEP_JOBS_MS if before is None else before
        async with self._db() as db:
            pruned = await db.execute(
                delete(CaptureJobRow).where(
                    CaptureJobRow.updated_at < before,
                    or_(CaptureJobRow.terminal, (CaptureJobRow.status == "completed") & CaptureJobRow.indexed),
                )
            )
            await db.commit()
            return int(pruned.rowcount or 0)  # type: ignore[attr-defined]


def _claimable(now: int, pending_grace_ms: int = 0) -> Any:
    """Work to claim: pending (``pending_grace_ms`` old at least), a retry whose backoff has
    passed, or a running job whose lease has run out."""
    return or_(
        (CaptureJobRow.status == "pending") & (CaptureJobRow.available_at <= now - pending_grace_ms),
        (CaptureJobRow.status == "failed") & ~CaptureJobRow.terminal & (CaptureJobRow.available_at <= now),
        (CaptureJobRow.status == "running") & (CaptureJobRow.lease_expires_at <= now),
    )


def _state_of() -> Any:
    return case(
        (CaptureJobRow.terminal, "terminal"),
        else_=CaptureJobRow.status,
    )


def _fenced(row: CaptureJobRow, lease: CaptureLease, now: int) -> None:
    """Grok's ``validate_lease``: this owner's running lease, this attempt, unexpired."""
    if not (
        row.status == "running"
        and row.tenant_id == lease.job.tenant_id
        and row.lease_owner == lease.owner
        and int(row.attempt) == lease.job.attempt
        and row.lease_expires_at is not None
        and int(row.lease_expires_at) > now
        and row.session_id == lease.job.session_id
        and int(row.from_turn) == lease.job.from_turn
        and int(row.through_turn) == lease.job.through_turn
    ):
        raise StaleLease()


async def _advance(db: AsyncSession, session_id: str, now: int) -> None:
    """Grok's ``advance_captured_cursor``, for both cursors: each over the contiguous run of
    resolved jobs after it, never past one still pending."""
    session = await db.scalar(
        select(CaptureSessionRow).where(CaptureSessionRow.session_id == session_id).with_for_update()
    )
    if session is None:
        return
    jobs = (
        await db.execute(
            select(CaptureJobRow.from_turn, CaptureJobRow.through_turn, CaptureJobRow.status, CaptureJobRow.terminal, CaptureJobRow.indexed)
            .where(CaptureJobRow.session_id == session_id, CaptureJobRow.through_turn > session.indexed)
            .order_by(CaptureJobRow.from_turn)
        )
    ).all()
    captured, indexed = int(session.captured), int(session.indexed)
    for start, through, status, terminal, landed in jobs:
        if start <= captured + 1 and (status == "completed" or terminal):
            captured = max(captured, int(through))
        if start <= indexed + 1 and (terminal or (status == "completed" and landed)):
            indexed = max(indexed, int(through))
    if (captured, indexed) != (int(session.captured), int(session.indexed)):
        session.captured, session.indexed, session.updated_at = captured, indexed, now


async def _revision(db: AsyncSession, notebook_id: str) -> str:
    facts = await db.scalar(select(NotebookRow.revision).where(NotebookRow.id == notebook_id))
    queued = await db.scalar(
        select(func.max(InboxRow.id)).where(InboxRow.notebook_id == notebook_id, InboxRow.promoted_at.is_(None))
    )
    return f"{int(facts or 0)}.{int(queued or 0)}"


def _canonical(outcome: dict[str, Any]) -> dict[str, Any]:
    observations = outcome.get("observations") or []
    retire = outcome.get("retire") or []
    if not isinstance(observations, list) or not isinstance(retire, list):
        raise CaptureInvalid("an outcome's observations and retirements are lists")
    context, author = outcome.get("context"), outcome.get("author")
    if context is not None and not isinstance(context, dict) or author is not None and not isinstance(author, str):
        raise CaptureInvalid("an outcome's context is an object and its author a string")
    try:  # refused now, not committed and then never kept
        for wire in observations:
            observation_in(wire).validate()
        context_in(context)
    except (ObservationInvalid, KeyError, TypeError, ValueError) as exc:
        raise CaptureInvalid(f"an outcome that cannot be kept: {exc}") from exc
    return {"observations": observations, "retire": [str(r) for r in retire], "context": context, "author": author}


def _job(row: CaptureJobRow) -> CaptureJob:
    return CaptureJob(
        job_id=row.job_id,
        tenant_id=row.tenant_id,
        session_id=row.session_id,
        notebook_id=row.notebook_id,
        run_ids=tuple(row.run_ids or ()),
        from_turn=int(row.from_turn),
        through_turn=int(row.through_turn),
        attempt=int(row.attempt),
    )


def _within_bytes(name: str, value: str, most: int) -> None:
    if not value or len(value.encode("utf-8", "replace")) > most or _CONTROLS.search(value):
        raise CaptureInvalid(f"{name} must hold 1..={most} bytes and no control character")
