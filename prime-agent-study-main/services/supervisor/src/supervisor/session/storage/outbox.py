"""Supervisor tool-invoke outbox — exactly-once completion for invoke_tool.

Implements idempotent tool invocation to handle crash recovery:
- ToolStart journaled before execution begins
- ToolEnd journaled after execution completes
- Cached results returned on retry

See Memory/adrs/ADR-004-tool-outbox-ownership.md for design rationale.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from supervisor.agent.events import ToolEnd, ToolStart
from supervisor.db.models import ToolOutboxRow
from supervisor.session.core.types import BeginOutcome

if TYPE_CHECKING:
    from supervisor.journal.writer import JournalWriter
    from supervisor.seams import ToolResult

__all__ = [
    "ToolOutbox",
    "idempotency_key",
    "result_to_wire",
    "wire_to_result",
]

logger = logging.getLogger(__name__)


# =============================================================================
# Wire Format Converters
# =============================================================================


def idempotency_key(session_id: str, tool_call_id: str) -> str:
    """Generate unique key for tool call deduplication.

    Args:
        session_id: The session context
        tool_call_id: The unique tool call identifier

    Returns:
        Composite key for idempotency lookup
    """
    return f"{session_id}:{tool_call_id}"


def result_to_wire(result: ToolResult) -> dict[str, Any]:
    """Convert ToolResult to wire format for storage.

    Args:
        result: The tool execution result

    Returns:
        Dictionary suitable for JSON storage
    """
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
    """Convert wire format back to ToolResult.

    Args:
        wire: Dictionary from storage

    Returns:
        Reconstructed ToolResult
    """
    # Import here to avoid circular dependency
    from supervisor.seams import ToolResult as TR

    if wire.get("ok"):
        return TR(ok=True, payload=wire.get("payload"))
    payload = wire.get("payload") if isinstance(wire.get("payload"), dict) else {}
    return TR(
        ok=False,
        error=wire.get("error") or payload.get("error"),
        code=wire.get("code") or payload.get("code"),
        payload=payload or None,
    )


# =============================================================================
# Tool Outbox Implementation
# =============================================================================


class ToolOutbox:
    """Exactly-once tool invocation tracker with crash recovery.

    Ensures tool calls are executed exactly once even across crashes:
    1. begin() writes ToolStart + pending row atomically
    2. Caller executes the tool
    3. complete() writes ToolEnd + marks done atomically

    On retry:
    - If pending: re-execute (crash mid-flight)
    - If done: return cached result

    Example:
        outbox = ToolOutbox(session_factory, writer)

        outcome = await outbox.begin(
            session_id="sess-1",
            tool_call_id="tc-1",
            tool_name="read_file",
            arguments={"path": "/tmp/foo"},
            trace_id="trace-123",
        )

        if outcome.action == "cached":
            return outcome.cached

        # Execute tool...
        result = await execute_tool(...)

        await outbox.complete(
            session_id="sess-1",
            tool_call_id="tc-1",
            tool_name="read_file",
            result=result,
            trace_id="trace-123",
        )
    """

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        writer: JournalWriter,
    ) -> None:
        """Initialize outbox with database and journal access.

        Args:
            session_factory: Async SQLAlchemy session factory
            writer: Journal writer for event logging
        """
        self._factory = session_factory
        self._writer = writer

    async def get_by_key(self, key: str) -> ToolOutboxRow | None:
        """Lookup outbox row by idempotency key.

        Args:
            key: The idempotency key from idempotency_key()

        Returns:
            ToolOutboxRow if found, None otherwise
        """
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
        """Start tool invocation with idempotency check.

        Creates ToolStart journal entry and pending outbox row,
        or returns cached result if already completed.

        Args:
            session_id: The session context
            tool_call_id: Unique identifier for this tool call
            tool_name: Name of the tool being invoked
            arguments: Tool arguments
            trace_id: Optional trace ID for observability

        Returns:
            BeginOutcome indicating whether to execute or use cache
        """
        key = idempotency_key(session_id, tool_call_id)

        # Fast path: check without lock
        existing = await self.get_by_key(key)
        if existing is not None and existing.status == "done" and existing.result is not None:
            logger.debug(
                "tool call cached (fast path)",
                extra={"session_id": session_id, "tool_call_id": tool_call_id},
            )
            return BeginOutcome(
                action="cached",
                tool_call_id=tool_call_id,
                cached=wire_to_result(existing.result),
            )
        if existing is not None and existing.status == "pending":
            # Crash mid-flight: ToolStart already journaled; re-execute once
            logger.debug(
                "tool call pending (crash recovery)",
                extra={"session_id": session_id, "tool_call_id": tool_call_id},
            )
            return BeginOutcome(action="execute", tool_call_id=tool_call_id)

        # Slow path: acquire lock and create atomically
        async with self._factory() as db:
            async with db.begin():
                again = (
                    await db.execute(
                        select(ToolOutboxRow)
                        .where(ToolOutboxRow.idempotency_key == key)
                        .with_for_update()
                    )
                ).scalar_one_or_none()

                if again is not None and again.status == "done" and again.result is not None:
                    logger.debug(
                        "tool call cached (slow path)",
                        extra={"session_id": session_id, "tool_call_id": tool_call_id},
                    )
                    return BeginOutcome(
                        action="cached",
                        tool_call_id=tool_call_id,
                        cached=wire_to_result(again.result),
                    )
                if again is not None and again.status == "pending":
                    return BeginOutcome(action="execute", tool_call_id=tool_call_id)

                # Create new pending entry
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

                logger.debug(
                    "tool call started",
                    extra={"session_id": session_id, "tool_call_id": tool_call_id, "tool_name": tool_name},
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
        """Complete tool invocation and store result.

        Creates ToolEnd journal entry and marks outbox row as done.
        Idempotent: returns stored result if already completed.

        Args:
            session_id: The session context
            tool_call_id: The tool call identifier from begin()
            tool_name: Name of the tool
            result: The execution result
            trace_id: Optional trace ID for observability

        Returns:
            The stored result (may be from previous completion)
        """
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

            # Already done: return cached
            if row is not None and row.status == "done" and row.result is not None:
                logger.debug(
                    "tool call already complete",
                    extra={"session_id": session_id, "tool_call_id": tool_call_id},
                )
                return wire_to_result(row.result)

            # Journal the completion
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
                # Should not happen in normal path; still record completion
                logger.warning(
                    "completing tool call without begin",
                    extra={"session_id": session_id, "tool_call_id": tool_call_id},
                )
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

            logger.debug(
                "tool call completed",
                extra={
                    "session_id": session_id,
                    "tool_call_id": tool_call_id,
                    "tool_name": tool_name,
                    "is_error": is_error,
                },
            )

        return result
