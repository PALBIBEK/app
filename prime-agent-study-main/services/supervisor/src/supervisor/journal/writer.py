"""Append-only journal writer. Allocates seq — never trusts the runtime."""

from __future__ import annotations

import hashlib
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, uuid4, uuid5

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from supervisor.agent.events import AgentEvent, MessageEnd, RunEnd
from supervisor.db.models import EventRow, SessionIndexRow, SessionRow
from supervisor.journal.serde import encode_event, event_type_name


def keyed_event_id(session_id: str, event_key: str) -> str:
    """Deterministic event id (fits EventRow.event_id) for an idempotent append."""
    return uuid5(NAMESPACE_URL, f"yal-journal:{session_id}:{event_key}").hex

# Default: offload payloads larger than 64 KiB.
_DEFAULT_BLOB_THRESHOLD = 64 * 1024


class OversizedPayloadError(RuntimeError):
    """Payload exceeds threshold but no blob_dir is configured."""


class JournalWriter:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        blob_dir: Path | str | None = None,
        blob_threshold: int = _DEFAULT_BLOB_THRESHOLD,
    ) -> None:
        self._factory = session_factory
        self._blob_dir = Path(blob_dir) if blob_dir is not None else None
        self._blob_threshold = blob_threshold
        if self._blob_dir is not None:
            self._blob_dir.mkdir(parents=True, exist_ok=True)

    async def ensure_session(
        self,
        session_id: str,
        *,
        runtime: str = "echo",
        tenant_id: str = "default",
        harness_version: str = "",
    ) -> None:
        async with self._factory() as db:
            existing = await db.get(SessionRow, session_id)
            if existing is not None:
                return
            db.add(
                SessionRow(
                    id=session_id,
                    tenant_id=tenant_id,
                    runtime=runtime,
                    harness_version=harness_version,
                    status="active",
                    last_seq=-1,
                )
            )
            # Flush parent first — no ORM relationship, so commit order is not guaranteed.
            await db.flush()
            db.add(
                SessionIndexRow(
                    session_id=session_id,
                    title=None,
                    message_count=0,
                    total_cost=0,
                    last_event_at=None,
                )
            )
            await db.commit()

    async def append(
        self,
        session_id: str,
        event: AgentEvent,
        *,
        turn_id: str | None,
        trace_id: str | None,
        event_key: str | None = None,
    ) -> int:
        """Allocate seq, commit the row, return seq. Durable before return.

        With ``event_key`` the append is idempotent: the event id is derived from
        the key, and appending the same key again returns the original seq. This
        is what lets a retried workflow activity journal safely.
        """
        event_id = None
        if event_key is not None:
            event_id = keyed_event_id(session_id, event_key)
            if (seq := await self.seq_of(session_id, event_id)) is not None:
                return seq
        return await self.append_batch(
            session_id, [(event, turn_id, trace_id)], event_ids=[event_id]
        )

    async def seq_of(self, session_id: str, event_id: str) -> int | None:
        async with self._factory() as db:
            seq = (
                await db.execute(
                    select(EventRow.seq).where(
                        EventRow.session_id == session_id, EventRow.event_id == event_id
                    )
                )
            ).scalar_one_or_none()
        return None if seq is None else int(seq)

    async def append_batch(
        self,
        session_id: str,
        events: list[tuple[AgentEvent, str | None, str | None]],
        *,
        event_ids: list[str | None] | None = None,
    ) -> int:
        """Atomically append n events. Returns the last allocated seq."""
        if not events:
            raise ValueError("append_batch requires at least one event")
        n = len(events)

        async with self._factory() as db:
            async with db.begin():
                # Serialize allocators under SQLite.
                await db.execute(
                    text("SELECT id FROM sessions WHERE id = :id"),
                    {"id": session_id},
                )
                result = await db.execute(
                    text(
                        "UPDATE sessions SET last_seq = last_seq + :n, updated_at = :now "
                        "WHERE id = :id RETURNING last_seq"
                    ),
                    {
                        "n": n,
                        "id": session_id,
                        "now": datetime.now(timezone.utc),
                    },
                )
                last_seq = result.scalar_one()
                first = int(last_seq) - n + 1
                now_ms = int(time.time() * 1000)
                index_update: RunEnd | None = None
                cost_delta = 0.0
                for i, (event, turn_id, trace_id) in enumerate(events):
                    seq = first + i
                    payload = self._store_payload(event)
                    db.add(
                        EventRow(
                            session_id=session_id,
                            seq=seq,
                            event_id=(event_ids[i] if event_ids else None) or uuid4().hex,
                            turn_id=turn_id,
                            type=event_type_name(event),
                            payload=payload,
                            ts_ms=now_ms,
                            trace_id=trace_id,
                        )
                    )
                    if isinstance(event, RunEnd):
                        index_update = event
                    if isinstance(event, MessageEnd) and event.usage is not None:
                        cost_delta += float(event.usage.cost_total)
                if index_update is not None or cost_delta:
                    await self._touch_index(
                        db,
                        session_id,
                        run_end=index_update,
                        cost_delta=cost_delta,
                        ts_ms=now_ms,
                    )
        return int(last_seq)

    async def append_in_session(
        self,
        db: AsyncSession,
        session_id: str,
        event: AgentEvent,
        *,
        turn_id: str | None,
        trace_id: str | None,
    ) -> int:
        """Append one event inside an open transaction (caller owns begin/commit)."""
        await db.execute(
            text("SELECT id FROM sessions WHERE id = :id"),
            {"id": session_id},
        )
        result = await db.execute(
            text(
                "UPDATE sessions SET last_seq = last_seq + 1, updated_at = :now "
                "WHERE id = :id RETURNING last_seq"
            ),
            {
                "id": session_id,
                "now": datetime.now(timezone.utc),
            },
        )
        seq = int(result.scalar_one())
        now_ms = int(time.time() * 1000)
        payload = self._store_payload(event)
        db.add(
            EventRow(
                session_id=session_id,
                seq=seq,
                event_id=uuid4().hex,
                turn_id=turn_id,
                type=event_type_name(event),
                payload=payload,
                ts_ms=now_ms,
                trace_id=trace_id,
            )
        )
        return seq

    async def _touch_index(
        self,
        db: AsyncSession,
        session_id: str,
        *,
        run_end: RunEnd | None,
        cost_delta: float,
        ts_ms: int,
    ) -> None:
        idx = await db.get(SessionIndexRow, session_id)
        if idx is None:
            idx = SessionIndexRow(session_id=session_id)
            db.add(idx)
        if run_end is not None:
            idx.message_count = len(run_end.messages)
        idx.total_cost = float(idx.total_cost or 0) + cost_delta
        idx.last_event_at = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)

    def _store_payload(self, event: AgentEvent) -> dict[str, Any]:
        encoded = encode_event(event)
        raw = json.dumps(encoded, separators=(",", ":"), default=str).encode()
        if len(raw) <= self._blob_threshold:
            return encoded
        if self._blob_dir is None:
            raise OversizedPayloadError(
                f"payload {len(raw)} bytes exceeds threshold {self._blob_threshold} "
                "and no blob_dir configured"
            )
        digest = hashlib.sha256(raw).hexdigest()
        path = self._blob_dir / digest
        if not path.exists():
            path.write_bytes(raw)
        return {"_type": event_type_name(event), "_blob_ref": digest}
