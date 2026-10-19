"""PostgreSQL audit logger — persistent audit trail with batching.

Provides database-backed audit logging with:
- Batch writes for performance
- Idempotent logging with ON CONFLICT
- Indexed queries for tail/filter operations
- Automatic table creation if needed

Reference: QM's postgres-audit-log.ts pattern.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import TYPE_CHECKING, Any, Final

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from supervisor.policy.types import AuditAction, AuditEvent


if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


__all__ = [
    "PostgresAuditLogger",
    "DEFAULT_BATCH_SIZE",
    "DEFAULT_FLUSH_INTERVAL_MS",
]


_log = logging.getLogger(__name__)

# Default batch size before flush
DEFAULT_BATCH_SIZE: Final[int] = 100

# Default flush interval in milliseconds
DEFAULT_FLUSH_INTERVAL_MS: Final[int] = 5_000

# SQL for creating audit table (if not exists)
CREATE_TABLE_SQL: Final[str] = """
CREATE TABLE IF NOT EXISTS audit_events (
    id BIGSERIAL PRIMARY KEY,
    event_id VARCHAR(64) UNIQUE NOT NULL,
    timestamp_ms BIGINT NOT NULL,
    action VARCHAR(64) NOT NULL,
    principal_id VARCHAR(128),
    session_id VARCHAR(128),
    resource VARCHAR(256),
    status VARCHAR(32) NOT NULL,
    detail JSONB,
    idempotency_key VARCHAR(128),
    CONSTRAINT audit_idempotency UNIQUE (idempotency_key)
);

CREATE INDEX IF NOT EXISTS idx_audit_timestamp ON audit_events (timestamp_ms DESC);
CREATE INDEX IF NOT EXISTS idx_audit_action ON audit_events (action, timestamp_ms DESC);
CREATE INDEX IF NOT EXISTS idx_audit_session ON audit_events (session_id, timestamp_ms DESC);
CREATE INDEX IF NOT EXISTS idx_audit_principal ON audit_events (principal_id, timestamp_ms DESC);
"""

# SQL for inserting event
INSERT_SQL: Final[str] = """
INSERT INTO audit_events
    (event_id, timestamp_ms, action, principal_id, session_id, resource, status, detail)
VALUES
    (:event_id, :timestamp_ms, :action, :principal_id, :session_id, :resource, :status, :detail)
ON CONFLICT (event_id) DO NOTHING
"""

# SQL for inserting with idempotency key
INSERT_ONCE_SQL: Final[str] = """
INSERT INTO audit_events
    (event_id, timestamp_ms, action, principal_id, session_id, resource, status, detail, idempotency_key)
VALUES
    (:event_id, :timestamp_ms, :action, :principal_id, :session_id, :resource, :status, :detail, :key)
ON CONFLICT (idempotency_key) DO NOTHING
"""

# SQL for tail query
TAIL_SQL: Final[str] = """
SELECT event_id, timestamp_ms, action, principal_id, session_id, resource, status, detail
FROM audit_events
WHERE (:action IS NULL OR action = :action)
ORDER BY timestamp_ms DESC
LIMIT :limit
"""

# SQL for filtered query
QUERY_SQL: Final[str] = """
SELECT event_id, timestamp_ms, action, principal_id, session_id, resource, status, detail
FROM audit_events
WHERE (:action IS NULL OR action = :action)
  AND (:principal_id IS NULL OR principal_id = :principal_id)
  AND (:session_id IS NULL OR session_id = :session_id)
  AND (:since_ms IS NULL OR timestamp_ms >= :since_ms)
  AND (:until_ms IS NULL OR timestamp_ms <= :until_ms)
ORDER BY timestamp_ms DESC
LIMIT :limit
OFFSET :offset
"""


def _now_ms() -> int:
    """Current time in milliseconds."""
    return int(time.time() * 1000)


class PostgresAuditLogger:
    """Database-backed audit logger with batching and deduplication.

    Features:
    - Batched writes for high throughput
    - Idempotent logging with ON CONFLICT
    - Optimized tail queries for dashboards
    - Full filter support for compliance

    Example:
        logger = PostgresAuditLogger(session_factory)

        # Log event (batched)
        await logger.log(event)

        # Log with idempotency (immediate)
        logged = await logger.log_once("key_123", event)

        # Query recent events
        events = await logger.tail(limit=50)

        # Query with filters
        events = await logger.query(
            action="approval_granted",
            since_ms=_now_ms() - 3600_000,
        )
    """

    __slots__ = (
        "_db",
        "_batch_size",
        "_flush_interval_ms",
        "_buffer",
        "_seen_keys",
        "_last_flush_ms",
        "_table_verified",
        "_flush_lock",
    )

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        batch_size: int = DEFAULT_BATCH_SIZE,
        flush_interval_ms: int = DEFAULT_FLUSH_INTERVAL_MS,
    ) -> None:
        """Initialize PostgreSQL audit logger.

        Args:
            session_factory: SQLAlchemy async session factory
            batch_size: Number of events to buffer before flush
            flush_interval_ms: Max time between flushes
        """
        self._db = session_factory
        self._batch_size = batch_size
        self._flush_interval_ms = flush_interval_ms
        self._buffer: list[AuditEvent] = []
        self._seen_keys: set[str] = set()
        self._last_flush_ms = _now_ms()
        self._table_verified = False
        self._flush_lock = asyncio.Lock()

    async def _ensure_table(self) -> None:
        """Ensure audit_events table exists."""
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
                    _log.exception("Failed to create audit_events table")
            else:
                # SQLite or other - skip table creation
                self._table_verified = True

    async def log(self, event: AuditEvent) -> None:
        """Log audit event with batching.

        Event is buffered and written in batches for performance.
        Call flush() to force immediate write.

        Args:
            event: Event to log
        """
        self._buffer.append(event)

        # Check if we should flush
        should_flush = (
            len(self._buffer) >= self._batch_size
            or (_now_ms() - self._last_flush_ms) >= self._flush_interval_ms
        )

        if should_flush:
            await self._flush()

    async def log_once(self, key: str, event: AuditEvent) -> bool:
        """Log event with idempotency key.

        Uses ON CONFLICT DO NOTHING for deduplication.
        Writes immediately (not batched).

        Args:
            key: Idempotency key
            event: Event to log

        Returns:
            True if logged, False if key already exists
        """
        # Check local cache first
        if key in self._seen_keys:
            return False

        await self._ensure_table()

        async with self._db() as db:
            try:
                result = await db.execute(
                    text(INSERT_ONCE_SQL),
                    {
                        "event_id": event.event_id,
                        "timestamp_ms": event.timestamp_ms,
                        "action": event.action.value,
                        "principal_id": event.principal_id,
                        "session_id": event.session_id,
                        "resource": event.resource,
                        "status": event.status,
                        "detail": json.dumps(event.detail) if event.detail else None,
                        "key": key,
                    },
                )
                await db.commit()

                # Check if row was inserted
                if result.rowcount > 0:
                    self._seen_keys.add(key)
                    return True
                return False

            except IntegrityError:
                # Key already exists
                await db.rollback()
                return False
            except Exception:
                _log.exception(f"Failed to log_once event {event.event_id}")
                await db.rollback()
                return False

    async def query(
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
        """Query audit events with filters.

        Args:
            action: Filter by action type
            principal_id: Filter by principal
            session_id: Filter by session
            since_ms: Filter events after timestamp
            until_ms: Filter events before timestamp
            limit: Max events to return
            offset: Skip first N events

        Returns:
            List of matching events
        """
        await self._ensure_table()

        async with self._db() as db:
            try:
                result = await db.execute(
                    text(QUERY_SQL),
                    {
                        "action": action,
                        "principal_id": principal_id,
                        "session_id": session_id,
                        "since_ms": since_ms,
                        "until_ms": until_ms,
                        "limit": limit,
                        "offset": offset,
                    },
                )
                rows = result.fetchall()

                return [self._row_to_event(row) for row in rows]

            except Exception:
                _log.exception("Failed to query audit events")
                return []

    async def tail(
        self,
        *,
        action: str | None = None,
        limit: int = 50,
    ) -> list[AuditEvent]:
        """Get most recent events (optimized for dashboards).

        Args:
            action: Optional filter by action type
            limit: Max events to return

        Returns:
            List of most recent events
        """
        await self._ensure_table()

        async with self._db() as db:
            try:
                result = await db.execute(
                    text(TAIL_SQL),
                    {"action": action, "limit": limit},
                )
                rows = result.fetchall()

                return [self._row_to_event(row) for row in rows]

            except Exception:
                _log.exception("Failed to tail audit events")
                return []

    async def flush(self) -> int:
        """Force flush buffered events to database.

        Returns:
            Number of events flushed
        """
        return await self._flush()

    async def _flush(self) -> int:
        """Internal flush with locking."""
        async with self._flush_lock:
            if not self._buffer:
                return 0

            await self._ensure_table()

            events = self._buffer[:]
            self._buffer.clear()
            self._last_flush_ms = _now_ms()

            async with self._db() as db:
                try:
                    for event in events:
                        await db.execute(
                            text(INSERT_SQL),
                            {
                                "event_id": event.event_id,
                                "timestamp_ms": event.timestamp_ms,
                                "action": event.action.value,
                                "principal_id": event.principal_id,
                                "session_id": event.session_id,
                                "resource": event.resource,
                                "status": event.status,
                                "detail": json.dumps(event.detail) if event.detail else None,
                            },
                        )
                    await db.commit()
                    return len(events)

                except Exception:
                    _log.exception(f"Failed to flush {len(events)} audit events")
                    await db.rollback()
                    # Re-add failed events to buffer (with limit to prevent infinite growth)
                    if len(self._buffer) < self._batch_size * 2:
                        self._buffer = events + self._buffer
                    return 0

    def _row_to_event(self, row: Any) -> AuditEvent:
        """Convert database row to AuditEvent."""
        return AuditEvent(
            event_id=row.event_id,
            timestamp_ms=row.timestamp_ms,
            action=AuditAction(row.action),
            principal_id=row.principal_id or "",
            session_id=row.session_id,
            resource=row.resource,
            status=row.status,
            detail=json.loads(row.detail) if row.detail else None,
        )

    @property
    def buffer_size(self) -> int:
        """Number of events currently buffered."""
        return len(self._buffer)

    @property
    def batch_size(self) -> int:
        """Configured batch size."""
        return self._batch_size

    @property
    def flush_interval_ms(self) -> int:
        """Configured flush interval."""
        return self._flush_interval_ms
