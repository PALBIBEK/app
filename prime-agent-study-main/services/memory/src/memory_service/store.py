"""Durable store — flush, read, fork, list (Postgres/SQLite)."""

from __future__ import annotations

import time
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from memory_service.models import MemorySessionRow, OutboxRow, SessionEventRow
from memory_service.types import (
    FORMAT_VERSION,
    MemoryError,
    Scope,
    SessionEvent,
    SessionHandle,
    SessionNotFound,
    SessionSummary,
)


class SeqConflict(MemoryError):
    code = "SEQ_CONFLICT"


class DurableStore:
    def __init__(self, factory: async_sessionmaker[AsyncSession]) -> None:
        self._factory = factory

    async def create_session(
        self,
        scope: Scope,
        *,
        session_id: str | None = None,
        parent_id: str | None = None,
        forked_at_seq: int | None = None,
        format_version: int = FORMAT_VERSION,
    ) -> SessionHandle:
        sid = session_id or str(uuid.uuid4())
        now = int(time.time() * 1000)
        async with self._factory() as db:
            db.add(
                MemorySessionRow(
                    id=sid,
                    tenant=scope.tenant,
                    subject=scope.subject,
                    namespace=scope.namespace,
                    format_version=format_version,
                    parent_id=parent_id,
                    forked_at_seq=forked_at_seq,
                    created_at=now,
                    closed_at=None,
                    status="active",
                    last_seq=-1,
                    lease_owner=None,
                    lease_until_ms=None,
                    fence=0,
                )
            )
            await db.commit()
        return SessionHandle(id=sid, scope=scope, format_version=format_version)

    async def get_session_row(self, session_id: str) -> MemorySessionRow:
        async with self._factory() as db:
            row = await db.get(MemorySessionRow, session_id)
            if row is None:
                raise SessionNotFound(f"SESSION_NOT_FOUND: {session_id}")
            # expire-safe copy fields by expunging
            db.expunge(row)
            return row

    async def flush_events(
        self,
        session_id: str,
        events: list[SessionEvent],
        *,
        outbox_items: list[dict[str, Any]] | None = None,
    ) -> int:
        """Persist events (+ optional outbox rows) in one transaction. Returns last seq."""
        if not events:
            return (await self.get_session_row(session_id)).last_seq
        async with self._factory() as db:
            async with db.begin():
                row = await db.get(MemorySessionRow, session_id, with_for_update=True)
                if row is None:
                    raise SessionNotFound(f"SESSION_NOT_FOUND: {session_id}")
                expected = row.last_seq + 1
                for ev in events:
                    if ev.seq != expected:
                        raise SeqConflict(
                            f"SEQ_CONFLICT: expected {expected}, got {ev.seq}",
                            code="SEQ_CONFLICT",
                        )
                    db.add(
                        SessionEventRow(
                            session_id=session_id,
                            seq=ev.seq,
                            type=ev.type,
                            time=ev.time,
                            data=ev.data if isinstance(ev.data, dict) else {"value": ev.data},
                        )
                    )
                    expected += 1
                row.last_seq = events[-1].seq
                for item in outbox_items or []:
                    db.add(
                        OutboxRow(
                            id=item["id"],
                            session_id=session_id,
                            idempotency_key=item["idempotency_key"],
                            tool_call_seq=item["tool_call_seq"],
                            payload=item["payload"],
                            status="pending",
                            created_at=int(time.time() * 1000),
                            result=None,
                        )
                    )
                last = events[-1].seq
            return last

    async def load_events(self, session_id: str) -> list[SessionEvent]:
        async with self._factory() as db:
            result = await db.execute(
                select(SessionEventRow)
                .where(SessionEventRow.session_id == session_id)
                .order_by(SessionEventRow.seq)
            )
            rows = result.scalars().all()
            return [
                SessionEvent(type=r.type, seq=r.seq, time=r.time, data=r.data) for r in rows
            ]

    async def set_status(self, session_id: str, status: str) -> None:
        async with self._factory() as db:
            row = await db.get(MemorySessionRow, session_id)
            if row is None:
                raise SessionNotFound(f"SESSION_NOT_FOUND: {session_id}")
            row.status = status
            await db.commit()

    async def fork(
        self,
        session_id: str,
        *,
        at_seq: int | None = None,
        new_id: str | None = None,
    ) -> SessionHandle:
        row = await self.get_session_row(session_id)
        events = await self.load_events(session_id)
        if at_seq is None:
            sliced = events
            fork_seq = row.last_seq if row.last_seq >= 0 else None
        else:
            sliced = [e for e in events if e.seq <= at_seq]
            fork_seq = at_seq
        scope = Scope(tenant=row.tenant, subject=row.subject, namespace=row.namespace)
        handle = await self.create_session(
            scope,
            session_id=new_id,
            parent_id=session_id,
            forked_at_seq=fork_seq,
            format_version=row.format_version,
        )
        if sliced:
            # renumber contiguous from 0
            renumbered = [
                SessionEvent(type=e.type, seq=i, time=e.time, data=e.data)
                for i, e in enumerate(sliced)
            ]
            await self.flush_events(handle.id, renumbered)
        return handle

    async def list_sessions(self, scope: Scope) -> list[SessionSummary]:
        async with self._factory() as db:
            result = await db.execute(
                select(MemorySessionRow)
                .where(
                    MemorySessionRow.tenant == scope.tenant,
                    MemorySessionRow.subject == scope.subject,
                    MemorySessionRow.namespace == scope.namespace,
                )
                .order_by(MemorySessionRow.created_at.desc())
            )
            rows = result.scalars().all()
            out: list[SessionSummary] = []
            for r in rows:
                count = r.last_seq + 1 if r.last_seq >= 0 else 0
                out.append(
                    SessionSummary(
                        id=r.id,
                        scope=Scope(tenant=r.tenant, subject=r.subject, namespace=r.namespace),
                        status=r.status,
                        created_at=r.created_at,
                        event_count=count,
                    )
                )
            return out
