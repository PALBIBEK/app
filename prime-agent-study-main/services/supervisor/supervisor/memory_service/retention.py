"""Tier 1 retention — archive out of the hot table; never silent drop; still loadable.

The archive is a compressed row in the memory database (``memory_session_archive``),
so any replica loads it. It used to be a JSONL file under the archiving replica's
``archive_dir`` (``ObjectArchive``), and the events left the database: no other
replica could load the session. Files from then are still read when there is no row.
"""

from __future__ import annotations

import gzip
import json
import time
from pathlib import Path

from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from supervisor.memory_service.metrics import METRICS
from supervisor.memory_service.models import (
    ExtractionJobRow,
    MemorySessionRow,
    OutboxRow,
    SessionArchiveRow,
    SessionEventRow,
)
from supervisor.memory_service.types import SessionEvent, SessionNotFound


class ObjectArchive:
    """Archives written as JSONL files by builds before the archive table: read only."""

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root)

    def path_for(self, session_id: str) -> Path:
        return self.root / f"{session_id}.jsonl"

    def read_events(self, session_id: str) -> list[SessionEvent]:
        path = self.path_for(session_id)
        if not path.is_file():
            raise FileNotFoundError(path)
        out: list[SessionEvent] = []
        with path.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                raw = json.loads(line)
                out.append(
                    SessionEvent(
                        type=raw["type"],
                        seq=int(raw["seq"]),
                        time=int(raw["time"]),
                        data=raw.get("data"),
                    )
                )
        return out

    def exists(self, session_id: str) -> bool:
        return self.path_for(session_id).is_file()


class RetentionService:
    def __init__(
        self,
        factory: async_sessionmaker[AsyncSession],
        archive: ObjectArchive | None = None,
        *,
        hot_days: int = 30,
    ) -> None:
        self._factory = factory
        self._archive = archive
        self.hot_days = hot_days

    async def archive_session(self, session_id: str) -> dict[str, int | str]:
        """Move the session's events out of the hot table into its archive row (in the same
        database, so every replica can load it), in one transaction; keep its header,
        status=archived. Archiving again folds in what was written since."""
        async with self._factory() as db, db.begin():
            row = await db.get(MemorySessionRow, session_id, with_for_update=True)
            if row is None:
                raise SessionNotFound(f"SESSION_NOT_FOUND: {session_id}")
            events = await self._archived(db, session_id) + await self._hot(db, session_id)
            existing = await db.get(SessionArchiveRow, session_id)
            if existing is None:
                existing = SessionArchiveRow(session_id=session_id)
                db.add(existing)
            existing.events = _pack(events)
            existing.count = len(events)
            existing.through_seq = events[-1].seq if events else -1
            existing.archived_at = int(time.time() * 1000)
            await db.execute(delete(SessionEventRow).where(SessionEventRow.session_id == session_id))
            row.status = "archived"
            row.closed_at = row.closed_at or int(time.time() * 1000)
        METRICS.inc("archive_sessions")
        return {"session_id": session_id, "archived_events": len(events), "archive": "database"}

    async def cold_sessions(self, *, limit: int = 100, now_ms: int | None = None) -> list[str]:
        """Sessions with hot events, none newer than ``hot_days``, that can move out.

        Left hot: a session leased to a live writer, one erased or corrupt, and one with
        extraction or outbox work still to do (both read its hot events)."""
        now = int(time.time() * 1000) if now_ms is None else now_ms
        cutoff = now - self.hot_days * 86_400_000
        last = (
            select(SessionEventRow.session_id, func.max(SessionEventRow.time).label("last"))
            .group_by(SessionEventRow.session_id)
            .subquery()
        )
        busy_jobs = select(ExtractionJobRow.session_id).where(ExtractionJobRow.status.in_(("pending", "running")))
        busy_outbox = select(OutboxRow.session_id).where(OutboxRow.status.in_(("pending", "running")))
        query = (
            select(MemorySessionRow.id)
            .join(last, last.c.session_id == MemorySessionRow.id)
            .where(
                last.c.last < cutoff,
                MemorySessionRow.status.not_in(("erased", "corrupt")),
                or_(MemorySessionRow.lease_until_ms.is_(None), MemorySessionRow.lease_until_ms < now),
                MemorySessionRow.id.not_in(busy_jobs),
                MemorySessionRow.id.not_in(busy_outbox),
            )
            .order_by(last.c.last)
            .limit(limit)
        )
        async with self._factory() as db:
            return [str(sid) for sid in await db.scalars(query)]

    async def archive_cold(self, *, limit: int = 100) -> int:
        """Archive the coldest sessions (``cold_sessions``); how many were archived."""
        done = 0
        for session_id in await self.cold_sessions(limit=limit):
            try:
                await self.archive_session(session_id)
                done += 1
            except SessionNotFound:
                continue  # erased meanwhile
        return done

    async def load_archived_events(self, session_id: str) -> list[SessionEvent]:
        """The archive, then whatever was written to the session after it was archived."""
        async with self._factory() as db:
            return await self._archived(db, session_id) + await self._hot(db, session_id)

    async def _archived(self, db: AsyncSession, session_id: str) -> list[SessionEvent]:
        row = await db.get(SessionArchiveRow, session_id)
        if row is not None:
            return _unpack(row.events)
        if self._archive is not None and self._archive.exists(session_id):
            return self._archive.read_events(session_id)  # archived by a build before the table
        return []

    @staticmethod
    async def _hot(db: AsyncSession, session_id: str) -> list[SessionEvent]:
        rows = await db.scalars(
            select(SessionEventRow).where(SessionEventRow.session_id == session_id).order_by(SessionEventRow.seq)
        )
        return [SessionEvent(type=r.type, seq=r.seq, time=r.time, data=r.data) for r in rows]


def _pack(events: list[SessionEvent]) -> bytes:
    lines = (json.dumps({"type": e.type, "seq": e.seq, "time": e.time, "data": e.data}) for e in events)
    return gzip.compress("\n".join(lines).encode())


def _unpack(blob: bytes) -> list[SessionEvent]:
    out: list[SessionEvent] = []
    for line in gzip.decompress(blob).decode().splitlines():
        if line.strip():
            raw = json.loads(line)
            out.append(SessionEvent(type=raw["type"], seq=int(raw["seq"]), time=int(raw["time"]), data=raw.get("data")))
    return out
