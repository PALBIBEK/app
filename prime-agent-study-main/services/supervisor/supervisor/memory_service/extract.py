"""Stingy async extraction — queue off the request path."""

from __future__ import annotations

import re
import time
import uuid
from dataclasses import dataclass
from typing import Protocol

from sqlalchemy import ColumnElement, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from supervisor.memory_service.bank import MemoryBank
from supervisor.memory_service.models import ExtractionJobRow, MemorySessionRow, SessionEventRow
from supervisor.memory_service.tier2_types import ItemSuperseded, NewMemoryItem
from supervisor.memory_service.types import Scope


@dataclass(frozen=True)
class ExtractedCandidate:
    content: str
    confidence: float
    source_seq: int
    kind: str  # preference | decision | correction | fact


class Extractor(Protocol):
    def extract(self, events: list[SessionEventRow]) -> list[ExtractedCandidate]: ...


_PREF = re.compile(
    r"(?i)\b(i (?:prefer|like|want|always use)|please (?:always|never)|my preference is)\b"
)
_DECISION = re.compile(r"(?i)\b(we (?:decided|chose|will use)|decision:|going with)\b")
_CORRECTION = re.compile(r"(?i)\b(no[, ]|actually[, ]|correction:|not X,? Y|i meant)\b")
_FACT = re.compile(
    r"(?i)\b(project (?:is|name)|repo(?:sitory)? (?:is|named)|api (?:is|called)|main project is)\b"
)


class StingyHeuristicExtractor:
    """
    Bias toward preferences, decisions, corrections, stable facts.
    Skips tool output / transient task chatter.
    """

    def extract(self, events: list[SessionEventRow]) -> list[ExtractedCandidate]:
        out: list[ExtractedCandidate] = []
        for ev in events:
            if ev.type not in ("user/message", "assistant/message"):
                continue
            data = ev.data if isinstance(ev.data, dict) else {}
            text = str(data.get("text") or data.get("content") or "").strip()
            if len(text) < 8 or len(text) > 500:
                continue
            kind: str | None = None
            conf = 0.7
            if _CORRECTION.search(text):
                kind, conf = "correction", 0.95
            elif _PREF.search(text):
                kind, conf = "preference", 0.9
            elif _DECISION.search(text):
                kind, conf = "decision", 0.85
            elif _FACT.search(text):
                kind, conf = "fact", 0.8
            if kind is None:
                continue
            out.append(
                ExtractedCandidate(
                    content=text[:400],
                    confidence=conf,
                    source_seq=ev.seq,
                    kind=kind,
                )
            )
        return out


CLAIM_TIMEOUT_MS = 10 * 60_000
"""How long a processor's claim on a job holds before another may retake it (a crashed processor)."""


def _claimable(now: int) -> ColumnElement[bool]:
    """Pending, or claimed by a processor that has not finished within the timeout."""
    return or_(
        ExtractionJobRow.status == "pending",
        (ExtractionJobRow.status == "running") & (ExtractionJobRow.claimed_at_ms < now - CLAIM_TIMEOUT_MS),
    )


class ExtractionQueue:
    """Extraction jobs, durable in ``extraction_job``.

    - A job covers the events after the session's previous job: the cursor is the
      database's (the highest ``seq_to`` queued), so a restart or another replica does
      not extract the same turns again.
    - A processor claims a job (one conditional UPDATE) before running it: of processors
      racing for one job, one wins. A claim older than ``CLAIM_TIMEOUT_MS`` belonged to a
      processor that died, and is retaken.
    """

    def __init__(
        self,
        factory: async_sessionmaker[AsyncSession],
        bank: MemoryBank,
        extractor: Extractor | None = None,
    ) -> None:
        self._factory = factory
        self._bank = bank
        self._extractor = extractor or StingyHeuristicExtractor()

    async def enqueue(self, session_id: str, seq_from: int, seq_to: int) -> str | None:
        if seq_to < seq_from:
            return None
        async with self._factory() as db:
            job_id = self._add(db, session_id, seq_from, seq_to)
            await db.commit()
        return job_id

    async def enqueue_through(self, session_id: str, seq_to: int) -> str | None:
        """Queue the session's events not yet covered by a job, through ``seq_to``."""
        async with self._factory() as db:
            covered = await db.scalar(
                select(func.max(ExtractionJobRow.seq_to)).where(ExtractionJobRow.session_id == session_id)
            )
            seq_from = -1 if covered is None else int(covered)
            if seq_to <= seq_from:
                return None
            job_id = self._add(db, session_id, seq_from + 1, seq_to)
            await db.commit()
        return job_id

    @staticmethod
    def _add(db: AsyncSession, session_id: str, seq_from: int, seq_to: int) -> str:
        job_id = str(uuid.uuid4())
        db.add(
            ExtractionJobRow(
                id=job_id,
                session_id=session_id,
                seq_from=seq_from,
                seq_to=seq_to,
                status="pending",
                created_at_ms=int(time.time() * 1000),
                error=None,
            )
        )
        return job_id

    async def backlog(self) -> dict[str, int]:
        """Jobs waiting (or running), the oldest one's age, and jobs that failed."""
        now = int(time.time() * 1000)
        async with self._factory() as db:
            waiting, oldest = (
                await db.execute(
                    select(func.count(), func.min(ExtractionJobRow.created_at_ms)).where(
                        ExtractionJobRow.status.in_(("pending", "running"))
                    )
                )
            ).one()
            failed = await db.scalar(
                select(func.count()).select_from(ExtractionJobRow).where(ExtractionJobRow.status == "error")
            )
        return {
            "extraction_backlog": int(waiting),
            "extraction_oldest_waiting_ms": 0 if oldest is None else max(0, now - int(oldest)),
            "extraction_jobs_failed": int(failed or 0),
        }

    async def _claim(self, job_id: str) -> bool:
        now = int(time.time() * 1000)
        async with self._factory() as db, db.begin():
            claimed = await db.execute(
                update(ExtractionJobRow)
                .where(ExtractionJobRow.id == job_id, _claimable(now))
                .values(status="running", claimed_at_ms=now)
            )
        return int(getattr(claimed, "rowcount", 0) or 0) == 1

    async def process_pending(self, *, limit: int = 50) -> int:
        """Run up to ``limit`` claimable jobs; returns how many this call ran.

        Errors are recorded on the job, never raised to callers."""
        async with self._factory() as db:
            result = await db.execute(
                select(ExtractionJobRow)
                .where(_claimable(int(time.time() * 1000)))
                .order_by(ExtractionJobRow.created_at_ms)
                .limit(limit)
            )
            jobs = list(result.scalars().all())
            for j in jobs:
                db.expunge(j)

        processed = 0
        for job in jobs:
            if not await self._claim(job.id):
                continue  # another processor took it
            try:
                await self._run_one(job)
                processed += 1
            except Exception as exc:  # noqa: BLE001 — must not fail the turn
                await self._finish(job.id, "error", str(exc)[:500])
        return processed

    async def _finish(self, job_id: str, status: str, error: str | None) -> None:
        async with self._factory() as db, db.begin():
            await db.execute(
                update(ExtractionJobRow)
                .where(ExtractionJobRow.id == job_id)
                .values(status=status, error=error, claimed_at_ms=None)
            )

    async def _run_one(self, job: ExtractionJobRow) -> None:
        async with self._factory() as db:
            sess = await db.get(MemorySessionRow, job.session_id)
            if sess is None:
                await self._finish(job.id, "error", "session missing")
                return
            scope = Scope(tenant=sess.tenant, subject=sess.subject, namespace=sess.namespace)
            result = await db.execute(
                select(SessionEventRow)
                .where(
                    SessionEventRow.session_id == job.session_id,
                    SessionEventRow.seq >= job.seq_from,
                    SessionEventRow.seq <= job.seq_to,
                )
                .order_by(SessionEventRow.seq)
            )
            events = list(result.scalars().all())
            for e in events:
                db.expunge(e)

        candidates = self._extractor.extract(events)
        kept = 0
        for cand in candidates:
            dup = await self._bank.find_near_duplicate(scope, cand.content)
            new_item = NewMemoryItem(
                content=cand.content,
                confidence=cand.confidence,
                source_session=job.session_id,
                source_seq=cand.source_seq,
                metadata={"kind": cand.kind},
            )
            if dup is None:
                await self._bank.write(scope, new_item)
                kept += 1
            elif cand.kind == "correction" or cand.confidence >= float(dup.confidence):
                if dup.content.strip().lower() != cand.content.strip().lower():
                    try:
                        await self._bank.supersede(dup.id, new_item, scope)
                    except ItemSuperseded:
                        continue  # superseded meanwhile: a newer version already stands
                    kept += 1

        await self._finish(job.id, "done", None)
        from supervisor.memory_service.metrics import METRICS

        METRICS.inc("extraction_jobs_done")
        METRICS.inc("extraction_items_kept", kept)
