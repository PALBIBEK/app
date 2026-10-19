"""Audit logger — policy decision logging with deduplication.

Provides audit trail for:
- Approval requests/responses
- Budget exceeded events
- Quota exceeded events
- Rate limiting events
- Command blocks
- Loop detection

Reference: QM's audit logging pattern.
"""

from __future__ import annotations

import time
from collections import OrderedDict
from typing import TYPE_CHECKING

from supervisor.policy.types import AuditEvent

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from supervisor.policy.audit.db_logger import PostgresAuditLogger


__all__ = ["AuditLogger", "InMemoryAuditLogger"]


# Default max events to keep in memory
DEFAULT_MAX_EVENTS = 50_000

# Default deduplication window (5 minutes)
DEFAULT_DEDUP_WINDOW_MS = 300_000


class InMemoryAuditLogger:
    """In-memory audit logger with deduplication.

    Features:
    - Bounded memory usage (max_events)
    - Event deduplication (log_once)
    - Query support with filtering

    Suitable for:
    - Development/testing
    - Single-process deployments
    - Low-volume audit trails

    For production, use database-backed AuditLogger.

    Example:
        logger = InMemoryAuditLogger(max_events=10_000)

        # Log event
        await logger.log(AuditEvent.now(
            event_id="evt_1",
            action=AuditAction.APPROVAL_GRANTED,
            principal_id="user_123",
            status="success",
        ))

        # Query recent events
        events = await logger.query(
            action="approval_granted",
            since_ms=time.time() * 1000 - 3600_000,
        )
    """

    __slots__ = ("_dedup_window_ms", "_events", "_max_events", "_seen_keys")

    def __init__(
        self,
        *,
        max_events: int = DEFAULT_MAX_EVENTS,
        dedup_window_ms: int = DEFAULT_DEDUP_WINDOW_MS,
    ) -> None:
        self._max_events = max_events
        self._dedup_window_ms = dedup_window_ms
        # Events stored newest-first for efficient query
        self._events: list[AuditEvent] = []
        # Deduplication: {key: timestamp_ms}
        self._seen_keys: OrderedDict[str, int] = OrderedDict()

    async def log(self, event: AuditEvent) -> None:
        """Log audit event.

        Args:
            event: Event to log
        """
        self._events.insert(0, event)

        # Trim if over limit
        while len(self._events) > self._max_events:
            self._events.pop()

    async def log_once(self, key: str, event: AuditEvent) -> bool:
        """Log event only if key not seen in dedup window.

        Useful for avoiding duplicate events for the same operation.

        Args:
            key: Deduplication key
            event: Event to log

        Returns:
            True if logged, False if deduplicated
        """
        now_ms = int(time.time() * 1000)

        # Clean old dedup entries
        self._clean_dedup(now_ms)

        # Check if seen
        if key in self._seen_keys:
            return False

        # Mark as seen
        self._seen_keys[key] = now_ms

        # Log event
        await self.log(event)
        return True

    async def query(
        self,
        *,
        action: str | None = None,
        principal_id: str | None = None,
        session_id: str | None = None,
        since_ms: int | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[AuditEvent]:
        """Query audit events.

        Args:
            action: Filter by action
            principal_id: Filter by principal
            session_id: Filter by session
            since_ms: Filter by timestamp (events after)
            limit: Max events to return
            offset: Skip first N matching events

        Returns:
            List of matching events
        """
        results: list[AuditEvent] = []
        skipped = 0

        for event in self._events:
            # Apply filters
            if action and event.action.value != action:
                continue
            if principal_id and event.principal_id != principal_id:
                continue
            if session_id and event.session_id != session_id:
                continue
            if since_ms and event.timestamp_ms < since_ms:
                continue

            # Handle offset
            if skipped < offset:
                skipped += 1
                continue

            results.append(event)

            if len(results) >= limit:
                break

        return results

    def _clean_dedup(self, now_ms: int) -> None:
        """Remove old entries from dedup cache."""
        cutoff = now_ms - self._dedup_window_ms
        while self._seen_keys:
            key, ts = next(iter(self._seen_keys.items()))
            if ts < cutoff:
                del self._seen_keys[key]
            else:
                break

    @property
    def event_count(self) -> int:
        """Number of stored events."""
        return len(self._events)

    def clear(self) -> None:
        """Clear all events."""
        self._events.clear()
        self._seen_keys.clear()


class AuditLogger:
    """Dual-layer audit logger with DB persistence and in-memory cache.

    Combines:
    - PostgresAuditLogger: Persistent storage with batching
    - InMemoryAuditLogger: Fast recent queries

    Falls back to memory-only for development/SQLite.

    Example:
        logger = AuditLogger(session_factory)
        await logger.log(event)

        # Query recent from memory (fast)
        events = await logger.query(limit=50)

        # Query all from DB (complete)
        events = await logger.query_db(since_ms=...)
    """

    __slots__ = ("_db", "_db_logger", "_memory")

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession] | None = None,
        *,
        max_memory_events: int = DEFAULT_MAX_EVENTS,
    ) -> None:
        self._db = session_factory
        self._memory = InMemoryAuditLogger(max_events=max_memory_events)
        self._db_logger: PostgresAuditLogger | None = None

        # Initialize DB logger if session factory provided
        if session_factory:
            from supervisor.policy.audit.db_logger import PostgresAuditLogger
            self._db_logger = PostgresAuditLogger(session_factory)

    async def log(self, event: AuditEvent) -> None:
        """Log audit event.

        Logs to both memory (fast queries) and database (persistence).

        Args:
            event: Event to log
        """
        # Always log to memory for fast recent queries
        await self._memory.log(event)

        # Persist to database if available
        if self._db_logger:
            await self._db_logger.log(event)

    async def log_once(self, key: str, event: AuditEvent) -> bool:
        """Log event only if key not seen recently.

        Uses DB idempotency if available, falls back to memory.

        Args:
            key: Deduplication key
            event: Event to log

        Returns:
            True if logged, False if deduplicated
        """
        # Check memory first (fast)
        memory_result = await self._memory.log_once(key, event)

        if not memory_result:
            return False

        # Also persist to DB if available
        if self._db_logger:
            # DB log_once handles its own idempotency
            await self._db_logger.log_once(key, event)

        return True

    async def query(
        self,
        *,
        action: str | None = None,
        principal_id: str | None = None,
        session_id: str | None = None,
        since_ms: int | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[AuditEvent]:
        """Query audit events from memory (fast, recent).

        For complete historical queries, use query_db().

        Args:
            action: Filter by action
            principal_id: Filter by principal
            session_id: Filter by session
            since_ms: Filter by timestamp
            limit: Max events
            offset: Skip first N

        Returns:
            List of matching events
        """
        return await self._memory.query(
            action=action,
            principal_id=principal_id,
            session_id=session_id,
            since_ms=since_ms,
            limit=limit,
            offset=offset,
        )

    async def query_db(
        self,
        *,
        action: str | None = None,
        principal_id: str | None = None,
        session_id: str | None = None,
        since_ms: int | None = None,
        until_ms: int | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[AuditEvent]:
        """Query audit events from database (complete, slower).

        For fast recent queries, use query() instead.

        Args:
            action: Filter by action
            principal_id: Filter by principal
            session_id: Filter by session
            since_ms: Filter events after timestamp
            until_ms: Filter events before timestamp
            limit: Max events
            offset: Skip first N

        Returns:
            List of matching events (or empty if no DB)
        """
        if not self._db_logger:
            # Fall back to memory
            return await self.query(
                action=action,
                principal_id=principal_id,
                session_id=session_id,
                since_ms=since_ms,
                limit=limit,
                offset=offset,
            )

        return await self._db_logger.query(
            action=action,
            principal_id=principal_id,
            session_id=session_id,
            since_ms=since_ms,
            until_ms=until_ms,
            limit=limit,
            offset=offset,
        )

    async def tail(
        self,
        *,
        action: str | None = None,
        limit: int = 50,
    ) -> list[AuditEvent]:
        """Get most recent events (optimized for dashboards).

        Uses DB if available, falls back to memory.

        Args:
            action: Optional filter by action type
            limit: Max events to return

        Returns:
            List of most recent events
        """
        if self._db_logger:
            return await self._db_logger.tail(action=action, limit=limit)

        # Fall back to memory query
        return await self._memory.query(action=action, limit=limit)

    async def flush(self) -> int:
        """Force flush buffered events to database.

        Returns:
            Number of events flushed (0 if no DB)
        """
        if self._db_logger:
            return await self._db_logger.flush()
        return 0

    @property
    def event_count(self) -> int:
        """Number of events in memory."""
        return self._memory.event_count

    @property
    def has_db(self) -> bool:
        """Whether database persistence is enabled."""
        return self._db_logger is not None
