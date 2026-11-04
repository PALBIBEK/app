"""Transactional outbox — exactly-once side effects."""

from __future__ import annotations

import time
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from supervisor.memory_service.models import OutboxRow, SessionEventRow
from supervisor.memory_service.types import SessionEvent, SessionNotFound

CLAIM_TIMEOUT_MS = 5 * 60_000
"""How long an executor's claim holds before the item may be claimed again (a crashed executor)."""

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
        """Items not done: pending, or claimed by an executor (perhaps one that crashed)."""
        async with self._factory() as db:
            result = await db.execute(
                select(OutboxRow)
                .where(OutboxRow.session_id == session_id, OutboxRow.status.in_(("pending", "running")))
                .order_by(OutboxRow.created_at)
            )
            rows = list(result.scalars().all())
            for r in rows:
                db.expunge(r)
            return rows

    async def _claim(self, item_id: str) -> bool:
        """Take ``item_id`` for this executor, atomically: of executors racing for it, one
        wins. A claim older than CLAIM_TIMEOUT_MS is a crashed executor's: it may be retaken."""
        now = int(time.time() * 1000)
        async with self._factory() as db, db.begin():
            claimed = await db.execute(
                update(OutboxRow)
                .where(
                    OutboxRow.id == item_id,
                    or_(
                        OutboxRow.status == "pending",
                        (OutboxRow.status == "running") & (OutboxRow.claimed_at < now - CLAIM_TIMEOUT_MS),
                    ),
                )
                .values(status="running", claimed_at=now)
            )
            return bool(claimed.rowcount)  # type: ignore[attr-defined]

    async def _release(self, item_id: str) -> None:
        async with self._factory() as db, db.begin():
            await db.execute(
                update(OutboxRow)
                .where(OutboxRow.id == item_id, OutboxRow.status == "running")
                .values(status="pending", claimed_at=None)
            )

    async def execute_pending(
        self,
        session_id: str,
        executor: Executor,
    ) -> list[dict[str, Any]]:
        """
        Run pending outbox items, each by one executor: an item is claimed before its
        side effect runs, so two executions at once (two replicas, a retried request) run
        it once; the other skips it. On success: insert tool/result, mark done. An
        executor that fails releases its claim (the item is pending again); one that
        crashes holds it until CLAIM_TIMEOUT_MS, then it may be claimed again.
        """
        results: list[dict[str, Any]] = []
        for item in await self.pending(session_id):
            if not await self._claim(item.id):
                continue  # another executor has it (or it finished meanwhile)
            try:
                outcome = executor(dict(item.payload))
                if hasattr(outcome, "__await__"):
                    outcome = await outcome  # type: ignore[misc]
            except BaseException:
                await self._release(item.id)
                raise
            assert isinstance(outcome, dict)

            async with self._factory() as db, db.begin():
                row = await db.get(OutboxRow, item.id, with_for_update=True)
                if row is None:
                    raise SessionNotFound(f"outbox missing {item.id}")
                if row.status == "done":
                    results.append(dict(row.result or outcome))
                    continue
                from supervisor.memory_service.models import MemorySessionRow

                sess = await db.get(MemorySessionRow, session_id, with_for_update=True)
                if sess is None:
                    raise SessionNotFound(f"SESSION_NOT_FOUND: {session_id}")
                next_seq = sess.last_seq + 1
                call_id = item.payload.get("call_id")
                db.add(
                    SessionEventRow(
                        session_id=session_id,
                        seq=next_seq,
                        type="tool/result",
                        time=int(time.time() * 1000),
                        data={
                            "call_id": call_id,
                            "status": "ok",
                            "result": outcome,
                            "idempotency_key": row.idempotency_key,
                        },
                    )
                )
                sess.last_seq = next_seq
                row.status = "done"
                row.result = outcome
                results.append(outcome)
        return results
