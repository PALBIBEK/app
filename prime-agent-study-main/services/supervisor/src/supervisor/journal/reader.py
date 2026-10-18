"""Journal reader — replay, unpaired ToolStart detection, latest history."""

from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from supervisor.agent.events import Envelope, RunEnd, ToolEnd, ToolStart
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

    async def unpaired_subagent_starts(self, session_id: str) -> list:
        """SubagentStart events whose run_id has no matching SubagentEnd."""
        from supervisor.session import unpaired_subagent_starts

        envs = await self.read(session_id, since=-1)
        return unpaired_subagent_starts(envs)

    async def unpaired_compaction_starts(self, session_id: str) -> list:
        """CompactionStart events whose compaction_id has no matching CompactionEnd."""
        from supervisor.session import unpaired_compaction_starts

        envs = await self.read(session_id, since=-1)
        return unpaired_compaction_starts(envs)

    async def session_tree(self, session_id: str):
        """Rebuild child/job tree from this session's journal."""
        from supervisor.session import build_session_tree

        envs = await self.read(session_id, since=-1)
        return build_session_tree(session_id, envs)

    async def last_seq(self, session_id: str) -> int:
        async with self._factory() as db:
            row = await db.get(SessionRow, session_id)
            if row is None:
                return -1
            return int(row.last_seq)

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

    def _load_payload(self, payload: dict) -> dict:
        ref = payload.get("_blob_ref")
        if ref is None:
            return payload
        if self._blob_dir is None:
            raise FileNotFoundError(f"blob ref {ref!r} but no blob_dir configured")
        raw = (self._blob_dir / ref).read_bytes()
        return json.loads(raw.decode())
