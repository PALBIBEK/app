"""Tier 1 retention — archive to object store; never silent drop; still loadable."""

from __future__ import annotations

import json
import time
from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from supervisor.memory_service.metrics import METRICS
from supervisor.memory_service.models import MemorySessionRow, SessionEventRow
from supervisor.memory_service.types import SessionEvent, SessionNotFound


class ObjectArchive:
    """Filesystem object store for archived session event JSONL."""

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def path_for(self, session_id: str) -> Path:
        return self.root / f"{session_id}.jsonl"

    def write_events(self, session_id: str, events: list[SessionEvent]) -> Path:
        path = self.path_for(session_id)
        with path.open("w", encoding="utf-8") as f:
            for ev in events:
                f.write(
                    json.dumps(
                        {"type": ev.type, "seq": ev.seq, "time": ev.time, "data": ev.data}
                    )
                    + "\n"
                )
        return path

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
        archive: ObjectArchive,
        *,
        hot_days: int = 30,
    ) -> None:
        self._factory = factory
        self._archive = archive
        self.hot_days = hot_days

    async def archive_session(self, session_id: str) -> dict[str, int | str]:
        """Move events to object store; keep session header; status=archived. Never delete header."""
        async with self._factory() as db:
            row = await db.get(MemorySessionRow, session_id)
            if row is None:
                raise SessionNotFound(f"SESSION_NOT_FOUND: {session_id}")
            result = await db.execute(
                select(SessionEventRow)
                .where(SessionEventRow.session_id == session_id)
                .order_by(SessionEventRow.seq)
            )
            rows = list(result.scalars().all())
            events = [
                SessionEvent(type=r.type, seq=r.seq, time=r.time, data=r.data) for r in rows
            ]
            self._archive.write_events(session_id, events)
            await db.execute(
                delete(SessionEventRow).where(SessionEventRow.session_id == session_id)
            )
            row.status = "archived"
            row.closed_at = row.closed_at or int(time.time() * 1000)
            # Keep last_seq so we know length; events live in archive
            await db.commit()
        METRICS.inc("archive_sessions")
        return {
            "session_id": session_id,
            "archived_events": len(events),
            "archive_path": str(self._archive.path_for(session_id)),
        }

    async def load_archived_events(self, session_id: str) -> list[SessionEvent]:
        return self._archive.read_events(session_id)

    async def archive_older_than(self, *, now_ms: int | None = None) -> list[str]:
        """Archive active sessions whose created_at is older than hot_days."""
        now = now_ms if now_ms is not None else int(time.time() * 1000)
        cutoff = now - self.hot_days * 86_400_000
        async with self._factory() as db:
            result = await db.execute(
                select(MemorySessionRow).where(
                    MemorySessionRow.status == "active",
                    MemorySessionRow.created_at < cutoff,
                )
            )
            ids = [r.id for r in result.scalars().all()]
        archived: list[str] = []
        for sid in ids:
            await self.archive_session(sid)
            archived.append(sid)
        return archived
