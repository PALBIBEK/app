"""Journal reader — replay, unpaired ToolStart detection, latest history."""

from __future__ import annotations

import json
import re
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from supervisor.agent.events import (
    CompactionEnd,
    CompactionStart,
    Envelope,
    JobEnd,
    JobStart,
    RunEnd,
    ToolEnd,
    ToolStart,
)
from supervisor.db.models import EventRow, SessionRow
from supervisor.journal.serde import decode_event


class JournalReader:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        blob_dir: Path | str | None = None,
    ) -> None:
        self._factory = session_factory
        self._blob_dir = Path(blob_dir) if blob_dir is not None else None

    @property
    def session_factory(self) -> async_sessionmaker[AsyncSession]:
        return self._factory

    async def read(self, session_id: str, *, since: int = -1) -> list[Envelope]:
        """Return envelopes with seq > since (exclusive), in order."""
        async with self._factory() as db:
            rows = (
                await db.execute(
                    select(EventRow)
                    .where(EventRow.session_id == session_id, EventRow.seq > since)
                    .order_by(EventRow.seq.asc())
                )
            ).scalars().all()
        return [self._to_envelope(row) for row in rows]

    async def hand_logs(self, session_id: str, ref: str) -> bytes | None:
        """A lost hand's saved logs, if one of the session's ``HandLost`` events names ``ref``."""
        async with self._factory() as db:
            rows = (
                await db.execute(
                    select(EventRow).where(EventRow.session_id == session_id, EventRow.type == "HandLost")
                )
            ).scalars().all()
        if not any(getattr(self._to_envelope(r).payload, "logs_ref", None) == ref for r in rows):
            return None
        try:
            return self.read_blob(ref)
        except FileNotFoundError:
            return None

    async def unpaired_tool_starts(self, session_id: str) -> list[ToolStart]:
        """ToolStart events whose tool_call_id has no matching ToolEnd."""
        envs = await self.read(session_id, since=-1)
        ended: set[str] = set()
        starts: dict[str, ToolStart] = {}
        for env in envs:
            if isinstance(env.payload, ToolStart):
                starts[env.payload.tool_call_id] = env.payload
            elif isinstance(env.payload, ToolEnd):
                ended.add(env.payload.tool_call_id)
        return [starts[cid] for cid in starts if cid not in ended]

    async def unpaired_compaction_starts(self, session_id: str) -> list[CompactionStart]:
        """CompactionStart events whose compaction_id has no matching CompactionEnd."""
        ended: set[str] = set()
        starts: dict[str, CompactionStart] = {}
        for env in await self.read(session_id, since=-1):
            if isinstance(env.payload, CompactionStart):
                starts[env.payload.compaction_id] = env.payload
            elif isinstance(env.payload, CompactionEnd):
                ended.add(env.payload.compaction_id)
        return [starts[cid] for cid in starts if cid not in ended]

    async def last_seq(self, session_id: str) -> int:
        async with self._factory() as db:
            row = await db.get(SessionRow, session_id)
            if row is None:
                return -1
            return int(row.last_seq)

    async def open_run(self, session_id: str) -> tuple[int, str | None] | None:
        """(seq, turn_id) of a RunStart whose run never finished (crashed), else None.

        A run ends with RunEnd, an ErrorEvent, or an aborted TurnEnd.
        """
        async with self._factory() as db:
            boundary = (
                await db.execute(
                    select(EventRow)
                    .where(
                        EventRow.session_id == session_id,
                        EventRow.type.in_(("RunStart", "RunEnd", "ErrorEvent")),
                    )
                    .order_by(EventRow.seq.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
            if boundary is None or boundary.type != "RunStart":
                return None
            run_start = (int(boundary.seq), boundary.turn_id)
            turn_ends = (
                await db.execute(
                    select(EventRow).where(
                        EventRow.session_id == session_id,
                        EventRow.type == "TurnEnd",
                        EventRow.seq > boundary.seq,
                    )
                )
            ).scalars().all()
        if any(
            getattr(decode_event(self._load_payload(row.payload)), "stop_reason", None) == "aborted"
            for row in turn_ends
        ):
            return None
        return run_start

    async def read_turn(self, session_id: str, turn_id: str) -> list[Envelope]:
        """One run's events, in order (workflow-driven runs tag every event with it)."""
        async with self._factory() as db:
            rows = (
                await db.execute(
                    select(EventRow)
                    .where(EventRow.session_id == session_id, EventRow.turn_id == turn_id)
                    .order_by(EventRow.seq.asc())
                )
            ).scalars().all()
        return [self._to_envelope(row) for row in rows]

    async def read_run_window(self, session_id: str, turn_id: str) -> list[Envelope]:
        """One run's events plus the session's own events (those with no run) that happened
        while it ran: a sandbox starting or being saved belongs to the run it served."""
        async with self._factory() as db:
            bounds = (
                await db.execute(
                    select(func.min(EventRow.seq), func.max(EventRow.seq)).where(
                        EventRow.session_id == session_id, EventRow.turn_id == turn_id
                    )
                )
            ).one()
            if bounds[0] is None:
                return []
            first, last = bounds
            ended = (
                await db.execute(
                    select(EventRow.seq).where(
                        EventRow.session_id == session_id, EventRow.turn_id == turn_id, EventRow.type == "RunEnd"
                    )
                )
            ).first()
            in_window = EventRow.seq >= first
            if ended is not None:
                in_window = in_window & (EventRow.seq <= last)
            rows = (
                await db.execute(
                    select(EventRow)
                    .where(
                        EventRow.session_id == session_id,
                        in_window,
                        (EventRow.turn_id == turn_id) | EventRow.turn_id.is_(None),
                    )
                    .order_by(EventRow.seq.asc())
                )
            ).scalars().all()
        return [self._to_envelope(row) for row in rows]

    async def event(self, session_id: str, event_id: str) -> Envelope | None:
        async with self._factory() as db:
            row = (
                await db.execute(
                    select(EventRow).where(
                        EventRow.session_id == session_id, EventRow.event_id == event_id
                    )
                )
            ).scalar_one_or_none()
        return None if row is None else self._to_envelope(row)

    async def latest_history(self, session_id: str) -> list[dict] | None:
        """Messages from the most recent RunEnd, or None if none."""
        async with self._factory() as db:
            row = (
                await db.execute(
                    select(EventRow)
                    .where(EventRow.session_id == session_id, EventRow.type == "RunEnd")
                    .order_by(EventRow.seq.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
        if row is None:
            return None
        payload = self._load_payload(row.payload)
        event = decode_event(payload)
        assert isinstance(event, RunEnd)
        return list(event.messages)

    async def read_types(
        self,
        session_ids: list[str],
        types: list[str],
        *,
        since_ms: int | None = None,
        until_ms: int | None = None,
        limit: int = 200_000,
    ) -> list[Envelope]:
        """Events of ``types`` across ``session_ids`` (oldest first), optionally in a time window.

        For views over many sessions (sandboxes, usage): one query, filtered by the
        event-type column, instead of reading each session's whole journal.
        """
        if not session_ids or not types:
            return []
        query = select(EventRow).where(EventRow.session_id.in_(session_ids), EventRow.type.in_(types))
        if since_ms is not None:
            query = query.where(EventRow.ts_ms >= since_ms)
        if until_ms is not None:
            query = query.where(EventRow.ts_ms < until_ms)
        async with self._factory() as db:
            rows = (
                await db.execute(query.order_by(EventRow.ts_ms.asc(), EventRow.seq.asc()).limit(limit))
            ).scalars().all()
        return [self._to_envelope(row) for row in rows]

    def _to_envelope(self, row: EventRow) -> Envelope:
        payload = decode_event(self._load_payload(row.payload))
        return Envelope(
            session_id=row.session_id,
            seq=int(row.seq),
            event_id=row.event_id,
            ts_ms=int(row.ts_ms),
            turn_id=row.turn_id,
            trace_id=row.trace_id,
            payload=payload,
        )

    async def unfinished_jobs(self, session_id: str) -> set[str]:
        """The session's background subagent jobs that started and have not ended."""
        events = await self.read_types([session_id], [JobStart.__name__, JobEnd.__name__])
        started = {e.payload.job_id for e in events if isinstance(e.payload, JobStart) and e.payload.kind == "subagent"}
        return started - {e.payload.job_id for e in events if isinstance(e.payload, JobEnd)}

    @property
    def blob_dir(self) -> Path | None:
        """Where large payloads are kept beside the journal (``JournalWriter.put_blob``)."""
        return self._blob_dir

    def read_blob(self, ref: str) -> bytes:
        """A blob stored with ``JournalWriter.put_blob``; ``FileNotFoundError`` if there is none."""
        if self._blob_dir is None or not re.fullmatch(r"[0-9a-f]{64}", ref):
            raise FileNotFoundError(ref)
        return (self._blob_dir / ref).read_bytes()

    def _load_payload(self, payload: dict) -> dict:
        ref = payload.get("_blob_ref")
        if ref is None:
            return payload
        if self._blob_dir is None:
            raise FileNotFoundError(f"blob ref {ref!r} but no blob_dir configured")
        raw = (self._blob_dir / ref).read_bytes()
        return json.loads(raw.decode())
