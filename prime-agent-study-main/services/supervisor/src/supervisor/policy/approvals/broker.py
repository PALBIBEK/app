"""Approval broker — persist, list, respond. Never blocks a worker on request.

Rewritten with:
- Proper async patterns
- Enum-based status instead of strings
- Pagination for list_pending
- Proper timezone handling
- Transaction safety with rollback

Reference: QM's approval broker pattern.
"""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Literal
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from supervisor.db.models import ApprovalRow
from supervisor.policy.errors import (
    ApprovalAlreadyResponded,
    ApprovalNotFound,
    ApprovalTimedOut,
    InvalidApprovalChoice,
)
from supervisor.policy.types import Approval, ApprovalStatus

if TYPE_CHECKING:
    from supervisor.policy.protocols import AuditLoggerProtocol


__all__ = ["ApprovalBroker"]


def _utc_now_ms() -> int:
    """Current UTC timestamp in milliseconds."""
    return int(time.time() * 1000)


def _datetime_to_ms(dt: datetime) -> int:
    """Convert datetime to milliseconds since epoch."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return int(dt.timestamp() * 1000)


class ApprovalBroker:
    """Manages human-in-the-loop approval requests.

    Non-blocking design: request() returns immediately with approval_id.
    Workers poll or use waiters for completion.

    Features:
    - Persistent storage via SQLAlchemy
    - Idempotent respond() (same choice is no-op)
    - Automatic expiration via expire_due()
    - Optional audit logging

    Example:
        broker = ApprovalBroker(session_factory)

        # Create approval
        approval_id = await broker.request(
            session_id="sess_123",
            turn_id="turn_1",
            prompt="Allow file write?",
            options=["yes", "no"],
            timeout_s=300,
        )

        # User responds
        approval = await broker.respond(approval_id, "yes")
    """

    __slots__ = ("_audit", "_db", "_waiters")

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        audit: AuditLoggerProtocol | None = None,
    ) -> None:
        self._db = session_factory
        self._audit = audit
        # In-process waiters for tests; HTTP uses polling
        self._waiters: dict[str, asyncio.Event] = {}

    async def request(
        self,
        session_id: str,
        turn_id: str | None,
        prompt: str,
        options: list[str],
        timeout_s: float,
        *,
        harness_request_id: str | None = None,
    ) -> str:
        """Record a pending approval and return immediately (non-blocking).

        Args:
            session_id: Session requesting approval
            turn_id: Current turn ID (optional)
            prompt: Human-readable approval prompt
            options: Allowed response options
            timeout_s: Timeout in seconds
            harness_request_id: Optional harness correlation ID

        Returns:
            approval_id for tracking
        """
        approval_id = uuid4().hex
        now = datetime.now(UTC)

        row = ApprovalRow(
            approval_id=approval_id,
            session_id=session_id,
            turn_id=turn_id,
            prompt=prompt,
            options=list(options),
            status=ApprovalStatus.PENDING.value,
            choice=None,
            timeout_s=float(timeout_s),
            harness_request_id=harness_request_id,
            created_at=now,
        )

        async with self._db() as db:
            db.add(row)
            await db.commit()

        return approval_id

    async def respond(
        self, approval_id: str, choice: str, *, responded_by: str | None = None
    ) -> Approval:
        """Respond to approval with choice.

        Args:
            approval_id: Approval to respond to
            choice: Selected option
            responded_by: The principal who answered, recorded for audit

        Returns:
            Updated approval

        Raises:
            ApprovalNotFound: Unknown approval_id
            ApprovalAlreadyResponded: Already responded (different choice)
            ApprovalTimedOut: Already timed out
            InvalidApprovalChoice: Choice not in options
        """
        async with self._db() as db:
            row = await db.get(ApprovalRow, approval_id)

            if row is None:
                raise ApprovalNotFound(approval_id)

            # Handle already-responded idempotently
            if row.status == ApprovalStatus.APPROVED.value:
                if row.choice == choice:
                    return self._to_approval(row)
                raise ApprovalAlreadyResponded(approval_id, current_choice=row.choice)

            if row.status == ApprovalStatus.DENIED.value:
                if row.choice == choice:
                    return self._to_approval(row)
                raise ApprovalAlreadyResponded(approval_id, current_choice=row.choice)

            if row.status == ApprovalStatus.TIMED_OUT.value:
                raise ApprovalTimedOut(approval_id)

            # Validate choice against options
            options = tuple(row.options or [])
            if options and choice not in options:
                raise InvalidApprovalChoice(approval_id, choice, options)

            # Determine status based on choice
            is_allow = choice.strip().lower() in {"yes", "allow", "approve", "ok", "y"}
            row.status = (
                ApprovalStatus.APPROVED.value
                if is_allow
                else ApprovalStatus.DENIED.value
            )
            row.choice = choice
            row.responded_by = responded_by
            row.responded_at = datetime.now(UTC)

            await db.commit()
            await db.refresh(row)
            approval = self._to_approval(row)

        # Wake any in-process waiters
        waiter = self._waiters.pop(approval_id, None)
        if waiter is not None:
            waiter.set()

        return approval

    async def get(self, approval_id: str) -> Approval | None:
        """Get approval by ID."""
        async with self._db() as db:
            row = await db.get(ApprovalRow, approval_id)
            if row is None:
                return None
            return self._to_approval(row)

    async def pending(
        self,
        session_id: str,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> list[Approval]:
        """List pending approvals for session with pagination."""
        async with self._db() as db:
            query = (
                select(ApprovalRow)
                .where(
                    ApprovalRow.session_id == session_id,
                    ApprovalRow.status == ApprovalStatus.PENDING.value,
                )
                .order_by(ApprovalRow.created_at.desc())
                .limit(limit)
                .offset(offset)
            )
            rows = (await db.execute(query)).scalars().all()

        return [self._to_approval(r) for r in rows]

    async def get_by_harness_request(
        self,
        session_id: str,
        harness_request_id: str,
    ) -> Approval | None:
        """Get latest approval for harness request ID (any status)."""
        async with self._db() as db:
            query = (
                select(ApprovalRow)
                .where(
                    ApprovalRow.session_id == session_id,
                    ApprovalRow.harness_request_id == harness_request_id,
                )
                .order_by(ApprovalRow.created_at.desc())
            )
            row = (await db.execute(query)).scalars().first()

        if row is None:
            return None
        return self._to_approval(row)

    async def list_pending_all(
        self,
        *,
        limit: int = 1000,
        offset: int = 0,
    ) -> list[Approval]:
        """List all pending approvals with pagination."""
        async with self._db() as db:
            query = (
                select(ApprovalRow)
                .where(ApprovalRow.status == ApprovalStatus.PENDING.value)
                .order_by(ApprovalRow.created_at.asc())
                .limit(limit)
                .offset(offset)
            )
            rows = (await db.execute(query)).scalars().all()

        return [self._to_approval(r) for r in rows]

    async def expire_due(
        self,
        *,
        now_ms: int | None = None,
        default_choice: Literal["deny", "allow"] = "deny",
        session_defaults: dict[str, Literal["deny", "allow"]] | None = None,
    ) -> list[Approval]:
        """Mark overdue pending approvals as timed_out.

        Args:
            now_ms: Current time in milliseconds (defaults to now)
            default_choice: Default choice for expired approvals
            session_defaults: Per-session default choice overrides

        Returns:
            List of expired approvals
        """
        now_ms = now_ms or _utc_now_ms()
        expired: list[Approval] = []

        async with self._db() as db:
            query = select(ApprovalRow).where(
                ApprovalRow.status == ApprovalStatus.PENDING.value
            )
            rows = (await db.execute(query)).scalars().all()

            for row in rows:
                created_ms = _datetime_to_ms(row.created_at)
                deadline_ms = created_ms + int(float(row.timeout_s) * 1000)

                if now_ms < deadline_ms:
                    continue

                # Get per-session default or global default
                choice = (session_defaults or {}).get(row.session_id, default_choice)

                row.status = ApprovalStatus.TIMED_OUT.value
                row.choice = choice
                row.responded_at = datetime.now(UTC)
                expired.append(self._to_approval(row))

            await db.commit()

        return expired

    async def time_out(self, approval_id: str, choice: str) -> Approval:
        """Time out one pending approval with ``choice``; a resolved one is returned as is.

        Lets a workflow's durable timer resolve its own approval, racing the
        sweeper harmlessly: both apply the same on_timeout policy.
        """
        async with self._db() as db:
            row = await db.get(ApprovalRow, approval_id)
            if row is None:
                raise ApprovalNotFound(approval_id)
            if row.status == ApprovalStatus.PENDING.value:
                row.status = ApprovalStatus.TIMED_OUT.value
                row.choice = choice
                row.responded_at = datetime.now(UTC)
                await db.commit()
            return self._to_approval(row)

    async def wait_for_response(
        self,
        approval_id: str,
        timeout_s: float | None = None,
    ) -> Approval | None:
        """Wait for approval response (for tests).

        Args:
            approval_id: Approval to wait for
            timeout_s: Optional timeout

        Returns:
            Approval if responded, None if timeout
        """
        # Check if already responded
        approval = await self.get(approval_id)
        if approval is None:
            raise ApprovalNotFound(approval_id)
        if approval.status != ApprovalStatus.PENDING:
            return approval

        # Create waiter
        event = asyncio.Event()
        self._waiters[approval_id] = event

        try:
            await asyncio.wait_for(event.wait(), timeout=timeout_s)
            return await self.get(approval_id)
        except TimeoutError:
            return None
        finally:
            self._waiters.pop(approval_id, None)

    def _to_approval(self, row: ApprovalRow) -> Approval:
        """Convert DB row to Approval dataclass."""
        return Approval(
            approval_id=row.approval_id,
            session_id=row.session_id,
            turn_id=row.turn_id,
            prompt=row.prompt,
            options=tuple(row.options or []),
            status=ApprovalStatus(row.status),
            choice=row.choice,
            timeout_s=int(float(row.timeout_s)),
            created_at_ms=_datetime_to_ms(row.created_at),
            harness_request_id=row.harness_request_id,
            responded_by=row.responded_by,
            responded_at_ms=_datetime_to_ms(row.responded_at) if row.responded_at else None,
        )
