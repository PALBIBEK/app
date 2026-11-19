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
and the worker resolves again. Commits that change one notebook take turns on its row in
``memory_capture_notebooks``, whose count is part of that revision, so two resolved
against the same revision never both pass.

A committed outcome holds the statements a session would keep: it is cleared once its
facts have landed (or will never land), and an erased owner's are scrubbed with their
notebooks (``forget_erased``).
"""

from __future__ import annotations

import asyncio
import collections
import hashlib
import json
import logging
import random
import re
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, Final, Literal

from sqlalchemy import and_, delete, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from supervisor.memory_service.capture.errors import (
    CaptureConflict,
    CaptureInvalid,
    StaleLease,
    StaleResolution,
    UnknownCaptureSession,
)
from supervisor.memory_service.capture.models import (
    CaptureJobRow,
    CaptureNotebookRow,
    CaptureSessionRow,
)
from supervisor.memory_service.notebooks.errors import (
    InvalidExpiry,
    InvalidObservation,
    MemoryLimitExceeded,
    NotebookArchived,
    NotebookNotFound,
)
from supervisor.memory_service.notebooks.models import InboxRow, NotebookRow
from supervisor.memory_service.notebooks.ops import PROMOTE_ATTEMPTS, NotebookOps, expiry_day
from supervisor.memory_service.notebooks.render import normalize
from supervisor.memory_service.notebooks.store import HANDLED_PAYLOAD
from supervisor.memory_service.notebooks.types import Provenance, capture_provenance
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
MAX_STALE_RELEASES: Final = 6
"""Claims a job gets before a retryable failure is taken as terminal: ours, beyond Grok's
(whose single-user queue retries for ever)."""
RETRY_BACKOFF_MS: Final = 5_000
MAX_RETRY_BACKOFF_MS: Final = 300_000
"""A failed job is claimable again after 5 s, doubling to 5 minutes."""
LAND_GRACE_MS: Final = 2_000
"""How long after its commit a job's outcome is left to the committing call to queue, before
``reconcile`` queues it (a crash between the two)."""
KEEP_JOBS_MS: Final = 7 * 24 * 3600 * 1000
ERASED: Final = "notebook erased"
"""An erased owner's job's diagnostic: whatever it said before may quote the model's words."""
VIEW_JOBS: Final = 200
VIEW_STATEMENTS: Final = 1_000
"""At most what one resolution reads of what is still to land: the newest committed outcomes
and queued statements (a notebook whose drainer lags never makes every capture read it all)."""
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

    @staticmethod
    async def _notebook_for_update(db: AsyncSession, notebook_id: str, now: int) -> CaptureNotebookRow:
        query = select(CaptureNotebookRow).where(CaptureNotebookRow.notebook_id == notebook_id).with_for_update()
        if (row := await db.scalar(query)) is None:
            try:
                async with db.begin_nested():
                    db.add(CaptureNotebookRow(notebook_id=notebook_id, commits=0, updated_at=now))
            except IntegrityError:
                pass  # created by a racing commit: it is read, locked, below
            row = await db.scalar(query)
        assert row is not None
        return row

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
                    .where(
                        CaptureJobRow.status == "running", ~CaptureJobRow.terminal, CaptureJobRow.lease_expires_at > now
                    )
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
        if job_id is not None and pending_grace_ms == 0:
            # The job its caller just queued, claimed at once: whatever another replica's clock
            # wrote as its ``available_at`` (a few ms ahead is a minute's wait for the sweeper).
            open_now = or_(_claimable(now), and_(CaptureJobRow.status == "pending", ~CaptureJobRow.terminal))
        else:
            open_now = _claimable(now, pending_grace_ms)
        query = select(CaptureJobRow).where(CaptureJobRow.tenant_id == tenant_id, open_now)
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
            exhausted: set[str] = set()  # sessions of the jobs failed for good here
            while True:
                if (row := await db.scalar(query)) is None:
                    for gone in sorted(exhausted):  # after the jobs, in one order, as every write
                        await _advance(db, gone, now)
                    await db.commit()
                    return None
                if row.status == "running":
                    row.failures = int(row.failures) + 1  # its holder's lease ran out: a claim lost
                if int(row.failures) < MAX_CAPTURE_ATTEMPTS:
                    break
                # Its every claim ended badly, the last with its lease run out (a burst that
                # kills or hangs its worker): never claimed again, and its range is resolved.
                row.status, row.terminal = "failed", True
                row.lease_owner = row.lease_expires_at = None
                row.last_error = f"its lease ran out, after {row.failures} claims ended badly"
                row.outcome = None
                row.queued = row.indexed = True
                row.updated_at = now
                exhausted.add(row.session_id)
                await db.flush()
            for gone in sorted(exhausted):
                await _advance(db, gone, now)
            row.status = "running"
            row.lease_owner = owner
            row.lease_expires_at = expires
            row.attempt = int(row.attempt) + 1
            row.last_error = None
            row.updated_at = now
            await db.commit()
            return CaptureLease(_job(row), owner, expires)

    async def renew(self, lease: CaptureLease, lease_s: float) -> CaptureLease:
        """Extend a valid lease while its holder still works on it (a long wait for its
        tenant's slot, a slow model): it runs out only when the holder stops renewing."""
        if not 1 <= lease_s <= MAX_LEASE_S:
            raise CaptureInvalid(f"a lease lasts 1..={MAX_LEASE_S} seconds")
        now = self._clock()
        async with self._db() as db:
            row = await db.get(CaptureJobRow, lease.job.job_id, with_for_update=True)
            if row is None:
                raise StaleLease()
            _fenced(row, lease, now)
            row.lease_expires_at = now + int(lease_s * 1000)
            row.updated_at = now
            await db.commit()
            return CaptureLease(lease.job, lease.owner, int(row.lease_expires_at))

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
                if int(row.attempt) != lease.job.attempt or row.tenant_id != lease.job.tenant_id:
                    raise StaleLease()  # committed under a later claim: a paused holder is fenced
                if row.outcome_hash != digest:
                    raise CaptureConflict("job was already committed with a different outcome")
                return CommitResult(digest, True)
            _fenced(row, lease, now)
            # Past its stale releases, what it resolved is kept as resolved: a rare
            # near-duplicate is consolidation's (G5), never a capture lost to contention.
            held_to_revision = resolved_at is not None and int(row.stale) < MAX_STALE_RELEASES
            if noop and held_to_revision and (actual := await _revision(db, row.notebook_id)) != resolved_at:
                # A burst found to repeat what was still to land: that may have gone since.
                assert resolved_at is not None
                raise StaleResolution(resolved_at, actual)
            if not noop:
                # After the job's lock (one order, every commit): commits into one notebook
                # take turns here, each counted, so a resolution read before one is stale after.
                counter = await self._notebook_for_update(db, row.notebook_id, now)
                if held_to_revision and (actual := await _revision(db, row.notebook_id)) != resolved_at:
                    assert resolved_at is not None
                    raise StaleResolution(resolved_at, actual)
                counter.commits = int(counter.commits) + 1
                counter.updated_at = now
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
            row.failures = int(row.failures) + 1
            final = terminal or int(row.failures) >= MAX_CAPTURE_ATTEMPTS
            row.status, row.terminal, row.last_error = "failed", final, error
            row.lease_owner = row.lease_expires_at = None
            row.available_at = now + min(MAX_RETRY_BACKOFF_MS, RETRY_BACKOFF_MS * 2 ** (int(row.failures) - 1))
            row.updated_at = now
            if final:
                await _advance(db, row.session_id, now)
            await db.commit()

    async def release_retryable(self, lease: CaptureLease, error: str, *, stale: bool = False) -> None:
        """Give an exact owner and attempt's lease back during cancellation: claimable again at
        once, without waiting for its deadline (Grok's ``release_retryable``). No failure is
        counted: nothing was wrong with the job. ``stale``: given back because the notebook
        changed under each of its resolutions (S3): it waits, longer each time, and after
        ``MAX_STALE_RELEASES`` its next commit is not held to its revision."""
        now = self._clock()
        delay_ms, stale_bump = 0, 0
        if stale:
            async with self._db() as db:
                before = await db.scalar(select(CaptureJobRow.stale).where(CaptureJobRow.job_id == lease.job.job_id))
            stale_bump = 1
            backoff = min(MAX_RETRY_BACKOFF_MS, RETRY_BACKOFF_MS * 2 ** int(before or 0))
            delay_ms = int(backoff * random.uniform(0.5, 1.5))  # apart from the others it raced
        async with self._db() as db:
            released = await db.execute(
                update(CaptureJobRow)
                .where(
                    CaptureJobRow.job_id == lease.job.job_id,
                    CaptureJobRow.tenant_id == lease.job.tenant_id,
                    CaptureJobRow.session_id == lease.job.session_id,
                    CaptureJobRow.status == "running",
                    CaptureJobRow.lease_owner == lease.owner,
                    CaptureJobRow.attempt == lease.job.attempt,
                )
                .values(
                    status="failed", lease_owner=None, lease_expires_at=None, available_at=now + delay_ms,
                    last_error=sanitize_failure(error), updated_at=now, stale=CaptureJobRow.stale + stale_bump,
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
        except (
            ObservationInvalid, InvalidObservation, InvalidExpiry, MemoryLimitExceeded, NotebookArchived, NotebookNotFound,
        ) as exc:
            logger.warning("capture job %s: its outcome cannot be kept: %s", job.job_id, type(exc).__name__)
            # The class alone: what it says may quote the outcome (the status routes show it).
            await self._resolve_unkept(job.job_id, f"{type(exc).__name__}: its outcome cannot be kept")
            return
        except Exception:  # the database, the embedder: ``reconcile`` queues it again
            logger.warning("capture job %s: queuing its outcome failed", job.job_id, exc_info=True)
            return
        await self._queued(job)

    async def _queued(self, job: CaptureJob) -> None:
        """Its outcome is in the inbox (or a person's change put a handled row under its key
        first: ``drop_unqueued``, and the insert found it): marked so."""
        async with self._db() as db:
            row = await db.get(CaptureJobRow, job.job_id, with_for_update=True)
            if row is not None and row.status == "completed" and not row.queued:
                row.queued, row.updated_at = True, self._clock()
            await db.commit()

    async def drop_unqueued(
        self,
        notebook_id: str,
        *,
        db: AsyncSession | None = None,
        gone: set[str] | None = None,
        revived: bool = False,
    ) -> int:
        """A person changed the notebook, in this transaction (``db``, its notebook locked),
        before it reads the queue to strip it: ``gone``, the words (normalized) no fact has
        any more (None: a clear, all of them); ``revived``, facts brought back (a restore).

        An outcome committed into it whose row is not in the inbox yet (its queuing still on
        the way, or failed, for reconciliation to retry) never meets that strip. One the
        change concerns (it restates a word gone, or retires facts, judged before facts came
        back) is dropped, and a handled row is put under its key: its queuing, whenever it
        comes, finds the key taken and lands nothing. One whose row went in first (the key
        is taken: this insert waits for it, if its insert is still open) is in the inbox
        before the strip reads it, and stripped as every queued capture is. An outcome the
        change does not concern is left to land. The jobs dropped."""
        if db is None:
            async with self._db() as own:
                count = await self.drop_unqueued(notebook_id, db=own, gone=gone, revived=revived)
                await own.commit()
                return count
        now = self._clock()
        rows = list(
            await db.scalars(
                select(CaptureJobRow)
                .where(CaptureJobRow.notebook_id == notebook_id, CaptureJobRow.status == "completed", ~CaptureJobRow.queued)
                .order_by(CaptureJobRow.job_id)
                .with_for_update()
            )
        )
        dropped = []
        for row in rows:
            if not _concerns(row.outcome or {}, gone, revived):
                continue
            taken = await db.execute(_handled_row(db, notebook_id, row.job_id, now))
            if taken.rowcount != 1:  # type: ignore[attr-defined]
                continue  # its own row went in first: the strip that follows reaches it
            row.status, row.terminal = "failed", True
            row.last_error = "a person changed the notebook before its facts were queued"
            row.outcome = None
            row.queued = row.indexed = True
            row.updated_at = now
            dropped.append(row)
        if dropped:
            await self._count_change(db, notebook_id, now)
        for session_id in sorted({r.session_id for r in dropped}):
            await _advance(db, session_id, now)
        return len(dropped)

    async def _count_change(self, db: AsyncSession, notebook_id: str, now: int) -> None:
        """What is still to land in the notebook changed without a fact or a queued row
        changing (a committed outcome dropped): counted, so a resolution read before is stale."""
        counter = await self._notebook_for_update(db, notebook_id, now)
        counter.commits = int(counter.commits) + 1
        counter.updated_at = now

    async def _resolve_unkept(self, job_id: str, error: str) -> None:
        now = self._clock()
        async with self._db() as db:
            row = await db.get(CaptureJobRow, job_id, with_for_update=True)
            if row is None or row.indexed:
                return
            row.status, row.terminal, row.last_error = "failed", True, error
            row.queued = row.indexed = True
            row.outcome = None  # never kept: nothing of it stays
            row.updated_at = now
            await self._count_change(db, row.notebook_id, now)  # a resolution may have counted on it
            await _advance(db, row.session_id, now)
            await db.commit()

    async def _mark(self, job_id: str, *, queued: bool) -> None:
        async with self._db() as db:
            await db.execute(
                update(CaptureJobRow).where(CaptureJobRow.job_id == job_id).values(queued=queued, updated_at=self._clock())
            )
            await db.commit()

    # --- landing (G2's inbox) ----------------------------------------------------------------

    async def mark_indexed(self, job_ids: Sequence[str], *, notebook_id: str | None = None) -> int:
        """These jobs' facts have landed (the inbox drainer promoted their rows): advance their
        sessions' ``indexed`` cursors. ``notebook_id``: where they landed; a key naming a job
        of another notebook (any caller may choose an inbox key) is not that job."""
        ids = [j for j in job_ids if j.startswith("cap-")]
        if not ids:
            return 0
        now = self._clock()
        these = [CaptureJobRow.job_id.in_(ids), CaptureJobRow.status == "completed"]
        if notebook_id is not None:
            these.append(CaptureJobRow.notebook_id == notebook_id)
        async with self._db() as db:
            sessions = list(await db.scalars(select(CaptureJobRow.session_id).where(*these, ~CaptureJobRow.indexed)))
            if not sessions:
                return 0
            await db.execute(
                update(CaptureJobRow)
                .where(*these)
                .values(queued=True, indexed=True, outcome=None, updated_at=now)  # its facts hold it now
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
                        CaptureJobRow.updated_at < now - LAND_GRACE_MS,  # not taken by another pass lately
                    )
                    .order_by(CaptureJobRow.completed_at)
                    .limit(RECONCILE_BATCH)
                    .with_for_update(skip_locked=True)
                )
            )
            for row in unqueued:  # this pass's: another replica's skips them a while
                row.updated_at = now
            await db.commit()
            queued = list(
                (await db.execute(
                    select(CaptureJobRow.job_id, CaptureJobRow.notebook_id, InboxRow.promoted_at, InboxRow.attempts)
                    .outerjoin(
                        InboxRow,
                        (InboxRow.notebook_id == CaptureJobRow.notebook_id)
                        & (InboxRow.idempotency_key == CaptureJobRow.job_id),
                    )
                    .where(
                        CaptureJobRow.status == "completed",
                        ~CaptureJobRow.indexed,
                        CaptureJobRow.queued,
                        # Only those whose row is no longer waiting: one still queued is the
                        # drainer's, and a backlog of them never starves this pass.
                        or_(InboxRow.id.is_(None), InboxRow.promoted_at.is_not(None)),
                    )
                    .order_by(CaptureJobRow.completed_at)
                    .limit(RECONCILE_BATCH)
                )).all()
            )
        repaired = 0
        for row in unqueued:
            await self._land(_job(row), row.outcome or {})
            repaired += 1
        landed: list[str] = []
        set_aside: list[str] = []
        for job_id, _notebook, promoted_at, attempts in queued:
            # Handled (landed or dropped), or gone (pruned, erased): never coming back. One set
            # aside (attempts at the limit) did not land: its range fails, and the barrier says
            # so; requeued (``notebooks.requeue``), its facts land all the same.
            (set_aside if promoted_at is not None and int(attempts or 0) >= PROMOTE_ATTEMPTS else landed).append(job_id)
        return repaired + await self.mark_indexed(landed) + await self._set_aside(set_aside)

    async def _set_aside(self, job_ids: Sequence[str]) -> int:
        if not job_ids:
            return 0
        now = self._clock()
        async with self._db() as db:
            rows = list(
                await db.scalars(
                    select(CaptureJobRow)
                    .where(CaptureJobRow.job_id.in_(job_ids), CaptureJobRow.status == "completed", ~CaptureJobRow.indexed)
                    .order_by(CaptureJobRow.job_id)
                    .with_for_update()
                )
            )
            for row in rows:
                row.status, row.terminal = "failed", True
                row.last_error = "its facts were set aside by the inbox: they land if requeued"
                row.queued = row.indexed = True
                row.outcome = None
                row.updated_at = now
            for session_id in sorted({r.session_id for r in rows}):
                await _advance(db, session_id, now)
            await db.commit()
        return len(rows)

    # --- reads -----------------------------------------------------------------------------

    async def cursors(self, *, tenant_id: str, session_id: str) -> CaptureCursors:
        async with self._db() as db:
            row = await db.get(CaptureSessionRow, session_id)
            if row is None or row.tenant_id != tenant_id:
                raise UnknownCaptureSession(session_id)
            return CaptureCursors(int(row.requested), int(row.captured), int(row.indexed))

    async def work_state(
        self, *, tenant_id: str, session_id: str, through_turn: int, after_turn: int = 0
    ) -> CaptureWorkState:
        """Content-free job state of the turns after ``after_turn`` through ``through_turn``
        (Grok's ``work_state``)."""
        in_range = (
            CaptureJobRow.tenant_id == tenant_id,
            CaptureJobRow.session_id == session_id,
            CaptureJobRow.from_turn <= through_turn,
            CaptureJobRow.through_turn > after_turn,
        )
        async with self._db() as db:
            counts: collections.Counter[str] = collections.Counter()
            grouped = select(CaptureJobRow.status, CaptureJobRow.terminal, func.count()).where(*in_range)
            for status, terminal, n in (await db.execute(grouped.group_by(CaptureJobRow.status, CaptureJobRow.terminal))).all():
                counts["terminal" if terminal else str(status)] += int(n)
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
        # The turns this barrier waits on (or, all indexed already, the last of them): an old
        # range that failed for good says nothing of the turns captured since.
        after = min(cursors.indexed, target - 1)
        while cursors.indexed < target and time.monotonic() < deadline:
            await asyncio.sleep(min(poll_s, max(0.0, deadline - time.monotonic())))
            cursors = await self.cursors(tenant_id=tenant_id, session_id=session_id)
        state = await self.work_state(tenant_id=tenant_id, session_id=session_id, through_turn=target, after_turn=after)
        if cursors.indexed >= target:
            return ("failed" if state.terminal else "success"), cursors, state
        return ("retry" if state.failed else "timeout"), cursors, state

    async def revision(self, notebook_id: str) -> str:
        """The notebook's revision as resolution sees it (S3): its facts' revision, its
        queue's newest capture and its committed captures, so landing, queuing or committing
        changes it."""
        async with self._db() as db:
            return await _revision(db, notebook_id)

    async def forget_erased(self, *, tenant_id: str) -> int:
        """Scrub the tenant's jobs whose notebook is gone (its owner erased): their outcomes
        deleted, every one resolved (failed for good, if it was not), so no session's cursor
        stalls on them. Run after ``erase_owner``; a job queued into a notebook erased
        meanwhile is resolved when it lands (``_land``: not found). The jobs scrubbed."""
        now = self._clock()
        gone = ~select(NotebookRow.id).where(NotebookRow.id == CaptureJobRow.notebook_id).exists()
        async with self._db() as db:
            rows = list(
                await db.scalars(
                    select(CaptureJobRow)
                    .where(
                        CaptureJobRow.tenant_id == tenant_id,
                        gone,
                        # Not scrubbed already (an earlier erasure's): never the same rows again.
                        or_(
                            CaptureJobRow.outcome.is_not(None),
                            ~CaptureJobRow.indexed,
                            (CaptureJobRow.status != "completed") & ~CaptureJobRow.terminal,
                            CaptureJobRow.last_error.is_not(None) & (CaptureJobRow.last_error != ERASED),
                        ),
                    )
                    .order_by(CaptureJobRow.job_id)
                    .with_for_update()
                )
            )
            for row in rows:
                if row.status != "completed" and not row.terminal:
                    row.status, row.terminal = "failed", True
                    row.lease_owner = row.lease_expires_at = None
                row.last_error = ERASED if row.last_error is not None or row.terminal else None
                row.outcome = None
                row.queued = row.indexed = True
                row.updated_at = now
            # Every write's order: jobs, then the notebook's counter, then sessions (``commit``).
            await db.execute(
                delete(CaptureNotebookRow).where(
                    CaptureNotebookRow.notebook_id.in_(sorted({r.notebook_id for r in rows}))
                )
            )
            for session_id in sorted({r.session_id for r in rows}):
                await _advance(db, session_id, now)
            await db.commit()
        return len(rows)

    async def view(self, notebook_id: str) -> tuple[str, list[tuple[str, Provenance]]]:
        """What a capture's resolution compares a burst with beyond the landed facts (S3):
        the revision, then every statement committed into the notebook and not landed yet,
        whether queued in its inbox or committed and not yet queued (the window between a
        commit and its queuing, which the revision already counts). Read in that order, a
        statement moving on between the reads is seen twice, never missed: one still to be
        queued is read before the inbox, and the facts are read after this."""
        revision = await self.revision(notebook_id)
        async with self._db() as db:
            outcomes = list(
                await db.scalars(
                    select(CaptureJobRow.outcome)
                    .where(
                        CaptureJobRow.notebook_id == notebook_id,
                        CaptureJobRow.status == "completed",
                        ~CaptureJobRow.queued,
                    )
                    .order_by(CaptureJobRow.completed_at.desc())
                    .limit(VIEW_JOBS)
                )
            )
        store = self._notebooks.store
        committed: list[tuple[str, Provenance]] = []
        if outcomes:
            home = (await store.get(notebook_id)).home
            for outcome in outcomes:
                # The provenance it will land with (``NotebookOps.observe``).
                provenance = capture_provenance(home, context_in((outcome or {}).get("context")))
                committed += [(str(o.get("statement", "")), provenance) for o in (outcome or {}).get("observations", ())]
        return revision, committed + await store.queued_with_provenance(notebook_id, limit=VIEW_STATEMENTS)

    async def prune(self, *, before: int | None = None) -> int:
        """Delete resolved jobs last changed before ``before`` (a week ago by default), each
        only once its session's ``indexed`` cursor has passed it: one above a turn still
        unresolved is the step that cursor takes when that turn resolves. Then the sessions
        left with no job and unchanged as long (an enqueue meanwhile changes its session, so
        Postgres's recheck of the locked row keeps it), and the counters of notebooks gone:
        never a live notebook's, whose count is part of its revision."""
        before = self._clock() - KEEP_JOBS_MS if before is None else before
        passed = (
            select(CaptureSessionRow.indexed)
            .where(CaptureSessionRow.session_id == CaptureJobRow.session_id)
            .scalar_subquery()
        )
        async with self._db() as db:
            pruned = await db.execute(
                delete(CaptureJobRow).where(
                    CaptureJobRow.updated_at < before,
                    or_(CaptureJobRow.terminal, (CaptureJobRow.status == "completed") & CaptureJobRow.indexed),
                    CaptureJobRow.through_turn <= passed,
                )
            )
            has_jobs = select(CaptureJobRow.job_id).where(CaptureJobRow.session_id == CaptureSessionRow.session_id).exists()
            await db.execute(delete(CaptureSessionRow).where(CaptureSessionRow.updated_at < before, ~has_jobs))
            live = select(NotebookRow.id).where(NotebookRow.id == CaptureNotebookRow.notebook_id).exists()
            await db.execute(delete(CaptureNotebookRow).where(~live))
            await db.commit()
            return int(pruned.rowcount or 0)  # type: ignore[attr-defined]


def _concerns(outcome: dict[str, Any], gone: set[str] | None, revived: bool) -> bool:
    """Whether a person's change concerns a committed outcome: a clear concerns them all;
    otherwise one restating words the change left no fact with, or (facts brought back,
    which its resolution never saw) one retiring facts."""
    if gone is None:
        return True
    if revived and outcome.get("retire"):
        return True
    return any(normalize(str(o.get("statement", ""))) in gone for o in outcome.get("observations") or ())


def _handled_row(db: AsyncSession, notebook_id: str, job_id: str, now: int) -> Any:
    """A handled inbox row under a job's key, unless the key is taken (``uq_memory_inbox_key``):
    one statement, on Postgres and SQLite alike, that never fails on the key."""
    values = {
        "notebook_id": notebook_id, "idempotency_key": job_id, "sensitivity": "unknown", "source_unknown": True,
        "received_at": now, "promoted_at": now, **HANDLED_PAYLOAD,
    }
    dialect = db.get_bind().dialect.name
    insert = postgres_insert if dialect == "postgresql" else sqlite_insert
    return insert(InboxRow).values(values).on_conflict_do_nothing(index_elements=["notebook_id", "idempotency_key"])


def _claimable(now: int, pending_grace_ms: int = 0) -> Any:
    """Work to claim: pending (``pending_grace_ms`` old at least), a retry whose backoff has
    passed, or a running job whose lease has run out."""
    return and_(
        # The partial index's own predicate, said outright: the planner reads only the work
        # still open (``ix_memory_capture_claimable``), never the week of resolved jobs kept.
        CaptureJobRow.status != "completed",
        ~CaptureJobRow.terminal,
        or_(
            (CaptureJobRow.status == "pending") & (CaptureJobRow.available_at <= now - pending_grace_ms),
            (CaptureJobRow.status == "failed") & (CaptureJobRow.available_at <= now),
            (CaptureJobRow.status == "running") & (CaptureJobRow.lease_expires_at <= now),
        ),
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
    # The newest queued row and how many are queued: a row queued raises the first, one
    # landing raises the facts' revision, and one set aside (the drainer gave up on it)
    # lowers the count, so whatever leaves the queue changes the revision too.
    newest, waiting = (
        await db.execute(
            select(func.max(InboxRow.id), func.count()).where(
                InboxRow.notebook_id == notebook_id, InboxRow.promoted_at.is_(None)
            )
        )
    ).one()
    commits = await db.scalar(select(CaptureNotebookRow.commits).where(CaptureNotebookRow.notebook_id == notebook_id))
    return f"{int(facts or 0)}.{int(newest or 0)}.{int(waiting or 0)}.{int(commits or 0)}"


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
        if (kept := context_in(context)) is not None:
            expiry_day(kept.expires_on)
    except (ObservationInvalid, InvalidExpiry, KeyError, TypeError, ValueError) as exc:
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
