"""Approval recovery — context preservation across restarts.

Stores context needed to resume approval workflow after process restart.
Context includes:
- Tool call state (tool_call_id, arguments)
- Session state snapshot
- Approval metadata

Now with full PostgreSQL persistence (QM pattern).

Reference: QM's StoredApproval and recoveredApprovalContext() pattern.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Final

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from supervisor.policy.errors import ApprovalRecoveryFailed
from supervisor.policy.types import ApprovalContext

if TYPE_CHECKING:
    pass


__all__ = [
    "ApprovalRecovery",
    "InMemoryApprovalRecovery",
    "PostgresApprovalRecovery",
]


_log = logging.getLogger(__name__)


def _now_ms() -> int:
    """Current time in milliseconds."""
    return int(time.time() * 1000)


class InMemoryApprovalRecovery:
    """In-memory approval context recovery.

    Suitable for single-process deployments or testing.
    Context is lost on process restart.

    Example:
        recovery = InMemoryApprovalRecovery()

        # Store context before waiting for approval
        context = ApprovalContext(
            approval_id="approval_123",
            session_id="sess_456",
            turn_id="turn_1",
            tool_call_id="call_789",
            tool_name="bash",
            arguments_hash="abc123",
        )
        await recovery.store_context("approval_123", context)

        # After restart, recover context
        context = await recovery.recover_context("approval_123")
    """

    __slots__ = ("_contexts",)

    def __init__(self) -> None:
        self._contexts: dict[str, ApprovalContext] = {}

    async def store_context(
        self,
        approval_id: str,
        context: ApprovalContext,
    ) -> None:
        """Store context for recovery.

        Args:
            approval_id: Approval to store context for
            context: Context to store
        """
        self._contexts[approval_id] = context

    async def recover_context(
        self,
        approval_id: str,
    ) -> ApprovalContext | None:
        """Recover stored context.

        Args:
            approval_id: Approval to recover

        Returns:
            Stored context or None if not found
        """
        return self._contexts.get(approval_id)

    async def clear_context(self, approval_id: str) -> None:
        """Clear stored context after completion.

        Args:
            approval_id: Approval to clear
        """
        self._contexts.pop(approval_id, None)

    async def list_contexts(
        self,
        session_id: str | None = None,
        *,
        limit: int = 100,
    ) -> list[ApprovalContext]:
        """List stored contexts.

        Args:
            session_id: Optional filter by session
            limit: Max contexts to return

        Returns:
            List of contexts
        """
        results = []
        for context in self._contexts.values():
            if session_id and context.session_id != session_id:
                continue
            results.append(context)
            if len(results) >= limit:
                break
        return results


# =============================================================================
# SQL Statements
# =============================================================================

# Create table SQL
CREATE_TABLE_SQL: Final[str] = """
CREATE TABLE IF NOT EXISTS approval_contexts (
    approval_id VARCHAR(64) PRIMARY KEY,
    session_id VARCHAR(128) NOT NULL,
    turn_id VARCHAR(128),
    tool_call_id VARCHAR(128),
    tool_name VARCHAR(128),
    arguments_hash VARCHAR(64),
    state_snapshot JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at TIMESTAMPTZ NOT NULL,
    recovered BOOLEAN DEFAULT FALSE
);

CREATE INDEX IF NOT EXISTS idx_approval_ctx_session ON approval_contexts (session_id);
CREATE INDEX IF NOT EXISTS idx_approval_ctx_expires ON approval_contexts (expires_at);
"""

# Insert or update context
UPSERT_SQL: Final[str] = """
INSERT INTO approval_contexts
    (approval_id, session_id, turn_id, tool_call_id, tool_name, arguments_hash, state_snapshot, expires_at)
VALUES
    (:approval_id, :session_id, :turn_id, :tool_call_id, :tool_name, :arguments_hash, :state_snapshot, :expires_at)
ON CONFLICT (approval_id) DO UPDATE SET
    session_id = EXCLUDED.session_id,
    turn_id = EXCLUDED.turn_id,
    tool_call_id = EXCLUDED.tool_call_id,
    tool_name = EXCLUDED.tool_name,
    arguments_hash = EXCLUDED.arguments_hash,
    state_snapshot = EXCLUDED.state_snapshot,
    expires_at = EXCLUDED.expires_at
"""

# Recover single context
RECOVER_SQL: Final[str] = """
SELECT approval_id, session_id, turn_id, tool_call_id, tool_name, arguments_hash, state_snapshot
FROM approval_contexts
WHERE approval_id = :approval_id
  AND expires_at > NOW()
  AND recovered = FALSE
"""

# Mark context as recovered
MARK_RECOVERED_SQL: Final[str] = """
UPDATE approval_contexts SET recovered = TRUE WHERE approval_id = :approval_id
"""

# List pending for session
LIST_PENDING_SQL: Final[str] = """
SELECT approval_id, session_id, turn_id, tool_call_id, tool_name, arguments_hash, state_snapshot
FROM approval_contexts
WHERE (:session_id IS NULL OR session_id = :session_id)
  AND expires_at > NOW()
  AND recovered = FALSE
ORDER BY created_at
LIMIT :limit
"""

# Delete expired contexts
CLEANUP_SQL: Final[str] = """
DELETE FROM approval_contexts WHERE expires_at < NOW()
"""


# =============================================================================
# PostgresApprovalRecovery
# =============================================================================

class PostgresApprovalRecovery:
    """PostgreSQL-backed approval context recovery (QM pattern).

    Full persistence for approval contexts across restarts.
    Uses UPSERT for idempotent storage.

    Example:
        recovery = PostgresApprovalRecovery(session_factory)

        # Store context
        context = ApprovalContext(
            approval_id="approval_123",
            session_id="sess_456",
            turn_id="turn_1",
            tool_call_id="call_789",
            tool_name="bash",
            arguments_hash="abc123",
            state_snapshot={"cursor": 42},
        )
        await recovery.store_context("approval_123", context, expires_in_s=300)

        # After restart, recover
        context = await recovery.recover_context("approval_123")
        if context:
            # Resume from snapshot
            cursor = context.state_snapshot.get("cursor", 0)
    """

    __slots__ = ("_db", "_table_verified")

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        self._db = session_factory
        self._table_verified = False

    async def _ensure_table(self) -> None:
        """Ensure approval_contexts table exists."""
        if self._table_verified:
            return

        async with self._db() as db:
            conn = await db.connection()
            dialect = conn.dialect.name

            if dialect == "postgresql":
                try:
                    await db.execute(text(CREATE_TABLE_SQL))
                    await db.commit()
                    self._table_verified = True
                except Exception:
                    _log.exception("Failed to create approval_contexts table")
            else:
                # SQLite or other - skip
                self._table_verified = True

    async def store_context(
        self,
        approval_id: str,
        context: ApprovalContext,
        *,
        expires_in_s: int = 300,
    ) -> None:
        """Store context for recovery in database.

        Args:
            approval_id: Approval to store context for
            context: Context to store
            expires_in_s: Seconds until context expires (default: 5 min)
        """
        await self._ensure_table()

        expires_at = datetime.now(timezone.utc).timestamp() + expires_in_s
        expires_dt = datetime.fromtimestamp(expires_at, timezone.utc)

        async with self._db() as db:
            try:
                await db.execute(
                    text(UPSERT_SQL),
                    {
                        "approval_id": approval_id,
                        "session_id": context.session_id,
                        "turn_id": context.turn_id,
                        "tool_call_id": context.tool_call_id,
                        "tool_name": context.tool_name,
                        "arguments_hash": context.arguments_hash,
                        "state_snapshot": json.dumps(context.state_snapshot) if context.state_snapshot else None,
                        "expires_at": expires_dt,
                    },
                )
                await db.commit()
            except Exception:
                _log.exception(f"Failed to store context for {approval_id}")
                await db.rollback()
                raise ApprovalRecoveryFailed(f"Failed to store context: {approval_id}")

    async def recover_context(
        self,
        approval_id: str,
    ) -> ApprovalContext | None:
        """Recover stored context from database.

        Marks context as recovered to prevent duplicate recovery.

        Args:
            approval_id: Approval to recover

        Returns:
            Stored context or None if not found/expired
        """
        await self._ensure_table()

        async with self._db() as db:
            try:
                result = await db.execute(
                    text(RECOVER_SQL),
                    {"approval_id": approval_id},
                )
                row = result.fetchone()

                if not row:
                    return None

                # Mark as recovered
                await db.execute(
                    text(MARK_RECOVERED_SQL),
                    {"approval_id": approval_id},
                )
                await db.commit()

                return ApprovalContext(
                    approval_id=row.approval_id,
                    session_id=row.session_id,
                    turn_id=row.turn_id,
                    tool_call_id=row.tool_call_id,
                    tool_name=row.tool_name,
                    arguments_hash=row.arguments_hash,
                    state_snapshot=json.loads(row.state_snapshot) if row.state_snapshot else None,
                )

            except Exception:
                _log.exception(f"Failed to recover context for {approval_id}")
                return None

    async def recover_all_pending(
        self,
        session_id: str,
    ) -> list[ApprovalContext]:
        """Recover all pending approvals for session.

        Args:
            session_id: Session to recover contexts for

        Returns:
            List of recoverable contexts
        """
        await self._ensure_table()

        async with self._db() as db:
            try:
                result = await db.execute(
                    text(LIST_PENDING_SQL),
                    {"session_id": session_id, "limit": 100},
                )
                rows = result.fetchall()

                contexts = []
                for row in rows:
                    contexts.append(ApprovalContext(
                        approval_id=row.approval_id,
                        session_id=row.session_id,
                        turn_id=row.turn_id,
                        tool_call_id=row.tool_call_id,
                        tool_name=row.tool_name,
                        arguments_hash=row.arguments_hash,
                        state_snapshot=json.loads(row.state_snapshot) if row.state_snapshot else None,
                    ))

                return contexts

            except Exception:
                _log.exception(f"Failed to list pending contexts for {session_id}")
                return []

    async def clear_context(self, approval_id: str) -> None:
        """Clear stored context (mark as recovered).

        Args:
            approval_id: Approval to clear
        """
        await self._ensure_table()

        async with self._db() as db:
            try:
                await db.execute(
                    text(MARK_RECOVERED_SQL),
                    {"approval_id": approval_id},
                )
                await db.commit()
            except Exception:
                _log.exception(f"Failed to clear context for {approval_id}")

    async def cleanup_expired(self) -> int:
        """Remove expired contexts.

        Returns:
            Number of contexts removed
        """
        await self._ensure_table()

        async with self._db() as db:
            try:
                result = await db.execute(text(CLEANUP_SQL))
                await db.commit()
                return result.rowcount
            except Exception:
                _log.exception("Failed to cleanup expired contexts")
                return 0


# =============================================================================
# Unified ApprovalRecovery
# =============================================================================

class ApprovalRecovery:
    """Dual-layer approval context recovery with DB and memory.

    Uses PostgreSQL for persistence, falls back to memory when unavailable.
    Similar to AuditLogger's dual-layer approach.

    Example:
        recovery = ApprovalRecovery(session_factory)

        # Store context
        await recovery.store_context("approval_123", context)

        # After restart, recover
        context = await recovery.recover_context("approval_123")
    """

    __slots__ = ("_db", "_memory", "_postgres")

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        self._db = session_factory
        self._memory = InMemoryApprovalRecovery()
        self._postgres: PostgresApprovalRecovery | None = None

        # Initialize Postgres recovery if session factory provided
        if session_factory:
            self._postgres = PostgresApprovalRecovery(session_factory)

    async def store_context(
        self,
        approval_id: str,
        context: ApprovalContext,
        *,
        expires_in_s: int = 300,
    ) -> None:
        """Store context for recovery.

        Stores in both memory and database for durability.

        Args:
            approval_id: Approval to store context for
            context: Context to store
            expires_in_s: Seconds until context expires
        """
        # Always store in memory for fast access
        await self._memory.store_context(approval_id, context)

        # Persist to DB if available
        if self._postgres:
            try:
                await self._postgres.store_context(approval_id, context, expires_in_s=expires_in_s)
            except Exception:
                _log.warning(f"Failed to persist context {approval_id} to DB, memory only")

    async def recover_context(
        self,
        approval_id: str,
    ) -> ApprovalContext | None:
        """Recover stored context.

        Tries memory first, then database.

        Args:
            approval_id: Approval to recover

        Returns:
            Stored context or None
        """
        # Try memory first (fast)
        context = await self._memory.recover_context(approval_id)
        if context:
            return context

        # Try DB
        if self._postgres:
            context = await self._postgres.recover_context(approval_id)
            if context:
                # Cache in memory for future access
                await self._memory.store_context(approval_id, context)
                return context

        return None

    async def clear_context(self, approval_id: str) -> None:
        """Clear stored context.

        Args:
            approval_id: Approval to clear
        """
        await self._memory.clear_context(approval_id)
        if self._postgres:
            await self._postgres.clear_context(approval_id)

    async def list_pending_recoveries(
        self,
        session_id: str | None = None,
        *,
        limit: int = 100,
    ) -> list[ApprovalContext]:
        """List contexts awaiting recovery.

        Uses DB if available for completeness.

        Args:
            session_id: Optional filter by session
            limit: Max contexts to return

        Returns:
            List of contexts
        """
        if self._postgres and session_id:
            return await self._postgres.recover_all_pending(session_id)

        return await self._memory.list_contexts(session_id, limit=limit)

    async def cleanup_expired(self) -> int:
        """Remove expired contexts from DB.

        Returns:
            Number of contexts removed
        """
        if self._postgres:
            return await self._postgres.cleanup_expired()
        return 0

    @property
    def has_db(self) -> bool:
        """Whether database persistence is enabled."""
        return self._postgres is not None


def _context_to_dict(context: ApprovalContext) -> dict[str, Any]:
    """Serialize context to dictionary for storage."""
    return {
        "approval_id": context.approval_id,
        "session_id": context.session_id,
        "turn_id": context.turn_id,
        "tool_call_id": context.tool_call_id,
        "tool_name": context.tool_name,
        "arguments_hash": context.arguments_hash,
        "state_snapshot": context.state_snapshot,
    }


def _dict_to_context(data: dict[str, Any]) -> ApprovalContext:
    """Deserialize context from dictionary."""
    return ApprovalContext(
        approval_id=data["approval_id"],
        session_id=data["session_id"],
        turn_id=data.get("turn_id"),
        tool_call_id=data.get("tool_call_id"),
        tool_name=data.get("tool_name"),
        arguments_hash=data.get("arguments_hash"),
        state_snapshot=data.get("state_snapshot"),
    )
