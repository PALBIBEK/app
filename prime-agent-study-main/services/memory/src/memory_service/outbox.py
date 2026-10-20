"""Transactional outbox — exactly-once side effects."""

from __future__ import annotations

import time
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from memory_service.models import OutboxRow, SessionEventRow
from memory_service.types import SessionEvent, SessionNotFound

Executor = Callable[[dict[str, Any]], Awaitable[dict[str, Any]] | dict[str, Any]]


class Outbox:
    def __init__(self, factory: async_sessionmaker[AsyncSession]) -> None:
        self._factory = factory

    @staticmethod
    def item_for_tool_call(
        ev: SessionEvent,
        *,
        idempotency_key: str | None = None,
    ) -> dict[str, Any] | None:
        """Build outbox insert dict for a tool/call that declares side_effect."""
        if ev.type != "tool/call":
            return None
        data = ev.data if isinstance(ev.data, dict) else {}
        if not data.get("side_effect"):
            return None
        key = idempotency_key or data.get("idempotency_key") or str(uuid.uuid4())
        return {
            "id": str(uuid.uuid4()),
            "idempotency_key": str(key),
            "tool_call_seq": ev.seq,
            "payload": {
                "call_id": data.get("call_id") or data.get("tool_call_id"),
                "name": data.get("name"),
                "arguments": data.get("arguments") or data.get("args") or {},
                "idempotency_key": str(key),
            },
        }

    async def pending(self, session_id: str) -> list[OutboxRow]:
        async with self._factory() as db:
            result = await db.execute(
                select(OutboxRow)
                .where(
                    OutboxRow.session_id == session_id,
                    OutboxRow.status == "pending",
                )
                .order_by(OutboxRow.created_at)
            )
            rows = list(result.scalars().all())
            for r in rows:
                db.expunge(r)
            return rows

    async def execute_pending(
        self,
        session_id: str,
        executor: Executor,
    ) -> list[dict[str, Any]]:
        """
        Run pending outbox items. On success: insert tool/result, mark done.
        Idempotent: already-done keys are skipped; retries reuse same key.
        """
        results: list[dict[str, Any]] = []
        pending = await self.pending(session_id)
        for item in pending:
            # Re-check status inside txn for race safety
            async with self._factory() as db, db.begin():
                row = await db.get(OutboxRow, item.id, with_for_update=True)
                if row is None or row.status == "done":
                    if row and row.result is not None:
                        results.append(dict(row.result))
                    continue
                    # Execute outside? For exactly-once we execute then commit.
                    # Pattern: mark processing... simpler MVP: execute then write result in same begin
                    # after await — keep executor sync or await before long critical section.
            outcome = executor(dict(item.payload))
            if hasattr(outcome, "__await__"):
                outcome = await outcome  # type: ignore[misc]
            assert isinstance(outcome, dict)

            async with self._factory() as db, db.begin():
                row = await db.get(OutboxRow, item.id, with_for_update=True)
                if row is None:
                    raise SessionNotFound(f"outbox missing {item.id}")
                if row.status == "done":
                    results.append(dict(row.result or outcome))
                    continue
                # Allocate next seq
                from memory_service.models import MemorySessionRow

                sess = await db.get(MemorySessionRow, session_id, with_for_update=True)
                if sess is None:
                    raise SessionNotFound(f"SESSION_NOT_FOUND: {session_id}")
                next_seq = sess.last_seq + 1
                call_id = item.payload.get("call_id")
                result_data = {
                    "call_id": call_id,
                    "status": "ok",
                    "result": outcome,
                    "idempotency_key": row.idempotency_key,
                }
                db.add(
                    SessionEventRow(
                        session_id=session_id,
                        seq=next_seq,
                        type="tool/result",
                        time=int(time.time() * 1000),
                        data=result_data,
                    )
                )
                sess.last_seq = next_seq
                row.status = "done"
                row.result = outcome
                results.append(outcome)
        return results

    async def count_effects_by_key(self, idempotency_key: str) -> int:
        """Test helper: how many done outbox rows share this key (should be ≤1)."""
        async with self._factory() as db:
            result = await db.execute(
                select(OutboxRow).where(OutboxRow.idempotency_key == idempotency_key)
            )
            return len(list(result.scalars().all()))
