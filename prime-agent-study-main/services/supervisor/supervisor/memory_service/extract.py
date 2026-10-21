"""Stingy async extraction — queue off the request path."""

from __future__ import annotations

import re
import time
import uuid
from dataclasses import dataclass
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from supervisor.memory_service.bank import MemoryBank
from supervisor.memory_service.models import ExtractionJobRow, MemorySessionRow, SessionEventRow
from supervisor.memory_service.tier2_types import NewMemoryItem
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


class ExtractionQueue:
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
        job_id = str(uuid.uuid4())
        async with self._factory() as db:
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
            await db.commit()
        return job_id


    async def process_pending(self, *, limit: int = 50) -> int:
        """Drain pending jobs. Errors are recorded on the job — never raised to callers."""
        processed = 0
        async with self._factory() as db:
            result = await db.execute(
                select(ExtractionJobRow)
                .where(ExtractionJobRow.status == "pending")
                .order_by(ExtractionJobRow.created_at_ms)
                .limit(limit)
            )
            jobs = list(result.scalars().all())
            for j in jobs:
                db.expunge(j)

        for job in jobs:
            try:
                await self._run_one(job)
                processed += 1
            except Exception as exc:  # noqa: BLE001 — must not fail the turn
                async with self._factory() as db:
                    row = await db.get(ExtractionJobRow, job.id)
                    if row is not None:
                        row.status = "error"
                        row.error = str(exc)[:500]
                        await db.commit()
        return processed

    async def _run_one(self, job: ExtractionJobRow) -> None:
        async with self._factory() as db:
            sess = await db.get(MemorySessionRow, job.session_id)
            if sess is None:
                row = await db.get(ExtractionJobRow, job.id)
                if row:
                    row.status = "error"
                    row.error = "session missing"
                    await db.commit()
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
                    await self._bank.supersede(dup.id, new_item, scope)
                    kept += 1

        async with self._factory() as db:
            row = await db.get(ExtractionJobRow, job.id)
            if row is not None:
                row.status = "done"
                row.error = None
                await db.commit()
        from supervisor.memory_service.metrics import METRICS

        METRICS.inc("extraction_jobs_done")
        METRICS.inc("extraction_items_kept", kept)
