"""Supervisor tool-invoke outbox — exactly-once completion for invoke_tool.

See Memory/adrs/ADR-004-tool-outbox-ownership.md.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from supervisor.agent.events import ToolEnd, ToolStart
from supervisor.db.models import ToolOutboxRow
from supervisor.journal.writer import JournalWriter
from supervisor.seams import ToolResult


def idempotency_key(session_id: str, tool_call_id: str) -> str:
    return f"{session_id}:{tool_call_id}"


def result_to_wire(result: ToolResult) -> dict[str, Any]:
    if result.ok:
        payload = result.payload if isinstance(result.payload, dict) else {"value": result.payload}
        return {"ok": True, "payload": payload, "is_error": False}
    return {
        "ok": False,
        "payload": {
            "error": result.error or "tool invoke failed",
            "code": result.code,
        },
        "is_error": True,
        "error": result.error,
        "code": result.code,
    }


def wire_to_result(wire: dict[str, Any]) -> ToolResult:
    if wire.get("ok"):
        return ToolResult(ok=True, payload=wire.get("payload"))
    payload = wire.get("payload") if isinstance(wire.get("payload"), dict) else {}
    return ToolResult(
        ok=False,
        error=wire.get("error") or payload.get("error"),
        code=wire.get("code") or payload.get("code"),
        payload=payload or None,
    )


@dataclass(frozen=True)
class BeginOutcome:
    action: Literal["execute", "cached"]
    tool_call_id: str
    cached: ToolResult | None = None


class ToolOutbox:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        writer: JournalWriter,
    ) -> None:
        self._factory = session_factory
        self._writer = writer

    async def get_by_key(self, key: str) -> ToolOutboxRow | None:
        async with self._factory() as db:
            row = (
                await db.execute(
                    select(ToolOutboxRow).where(ToolOutboxRow.idempotency_key == key)
                )
            ).scalar_one_or_none()
            if row is not None:
                db.expunge(row)
            return row

    async def begin(
        self,
        session_id: str,
        *,
        tool_call_id: str,
        tool_name: str,
        arguments: dict[str, Any],
        trace_id: str | None,
    ) -> BeginOutcome:
        """Ensure ToolStart + pending outbox row, or return cached done result."""
        key = idempotency_key(session_id, tool_call_id)
        existing = await self.get_by_key(key)
        if existing is not None and existing.status == "done" and existing.result is not None:
            return BeginOutcome(
                action="cached",
                tool_call_id=tool_call_id,
                cached=wire_to_result(existing.result),
            )
        if existing is not None and existing.status == "pending":
            # Crash mid-flight: ToolStart already journaled; re-execute once.
            return BeginOutcome(action="execute", tool_call_id=tool_call_id)

        async with self._factory() as db, db.begin():
            again = (
                await db.execute(
                    select(ToolOutboxRow)
                    .where(ToolOutboxRow.idempotency_key == key)
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if again is not None and again.status == "done" and again.result is not None:
                return BeginOutcome(
                    action="cached",
                    tool_call_id=tool_call_id,
                    cached=wire_to_result(again.result),
                )
            if again is not None and again.status == "pending":
                return BeginOutcome(action="execute", tool_call_id=tool_call_id)

            await self._writer.append_in_session(
                db,
                session_id,
                ToolStart(
                    tool_call_id=tool_call_id,
                    tool_name=tool_name,
                    args=dict(arguments),
                ),
                turn_id=None,
                trace_id=trace_id,
            )
            db.add(
                ToolOutboxRow(
                    id=uuid4().hex,
                    session_id=session_id,
                    tool_call_id=tool_call_id,
                    idempotency_key=key,
                    tool_name=tool_name,
                    arguments=dict(arguments),
                    status="pending",
                    result=None,
                )
            )
        return BeginOutcome(action="execute", tool_call_id=tool_call_id)

    async def complete(
        self,
        session_id: str,
        *,
        tool_call_id: str,
        tool_name: str,
        result: ToolResult,
        trace_id: str | None,
    ) -> ToolResult:
        """Journal ToolEnd + mark outbox done (idempotent if already done)."""
        key = idempotency_key(session_id, tool_call_id)
        wire = result_to_wire(result)
        end_payload = wire["payload"] if isinstance(wire.get("payload"), dict) else {}
        is_error = bool(wire.get("is_error"))

        async with self._factory() as db, db.begin():
            row = (
                await db.execute(
                    select(ToolOutboxRow)
                    .where(ToolOutboxRow.idempotency_key == key)
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if row is not None and row.status == "done" and row.result is not None:
                return wire_to_result(row.result)

            await self._writer.append_in_session(
                db,
                session_id,
                ToolEnd(
                    tool_call_id=tool_call_id,
                    tool_name=tool_name,
                    result=end_payload,
                    is_error=is_error,
                ),
                turn_id=None,
                trace_id=trace_id,
            )
            if row is None:
                # Should not happen in normal path; still record completion.
                db.add(
                    ToolOutboxRow(
                        id=uuid4().hex,
                        session_id=session_id,
                        tool_call_id=tool_call_id,
                        idempotency_key=key,
                        tool_name=tool_name,
                        arguments={},
                        status="done",
                        result=wire,
                    )
                )
            else:
                row.status = "done"
                row.result = wire
        return result
