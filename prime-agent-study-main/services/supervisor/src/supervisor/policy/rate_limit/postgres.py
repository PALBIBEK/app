"""Distributed rate limiter — Postgres-backed sliding window.

Provides cluster-wide rate limiting using Postgres:
- Atomic increment via UPSERT
- Sliding window with periodic cleanup
- Works across multiple replicas

Uses a dedicated rate_limits table (or creates it dynamically).

Reference: QM's distributed rate limiting pattern.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from supervisor.policy.errors import RateLimitExceeded
from supervisor.policy.types import RateDecision, RateDecisionStatus, DEFAULT_RATE_WINDOW_MS


if TYPE_CHECKING:
    pass


__all__ = ["PostgresRateLimiter"]


class PostgresRateLimiter:
    """Distributed rate limiter using Postgres.

    Uses atomic UPSERT to track request counts per window.
    Window key includes timestamp bucket for sliding window effect.

    Table schema (created if not exists):
        rate_limits (
            principal_id TEXT,
            window_key TEXT,
            count INTEGER,
            created_at TIMESTAMP,
            PRIMARY KEY (principal_id, window_key)
        )

    Example:
        limiter = PostgresRateLimiter(
            session_factory,
            max_per_window=100,
            window_ms=60_000,
        )

        decision = await limiter.check_and_record("user_123")
        if not decision.allowed:
            raise RateLimitExceeded(...)
    """

    __slots__ = ("_db", "_max", "_window_ms", "_table_name", "_initialized")

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        max_per_window: int,
        window_ms: int = DEFAULT_RATE_WINDOW_MS,
        table_name: str = "rate_limits",
    ) -> None:
        """Initialize distributed rate limiter.

        Args:
            session_factory: SQLAlchemy async session factory
            max_per_window: Maximum requests per window
            window_ms: Window duration in milliseconds
            table_name: Table name for rate limit storage
        """
        if max_per_window <= 0:
            raise ValueError("max_per_window must be positive")
        if window_ms <= 0:
            raise ValueError("window_ms must be positive")

        self._db = session_factory
        self._max = max_per_window
        self._window_ms = window_ms
        self._table_name = table_name
        self._initialized = False

    async def _ensure_table(self, db: AsyncSession) -> None:
        """Ensure rate_limits table exists."""
        if self._initialized:
            return

        conn = await db.connection()
        dialect = conn.dialect.name

        if dialect == "postgresql":
            await db.execute(text(f"""
                CREATE TABLE IF NOT EXISTS {self._table_name} (
                    principal_id TEXT NOT NULL,
                    window_key TEXT NOT NULL,
                    count INTEGER NOT NULL DEFAULT 1,
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                    PRIMARY KEY (principal_id, window_key)
                )
            """))
        else:
            # SQLite fallback
            await db.execute(text(f"""
                CREATE TABLE IF NOT EXISTS {self._table_name} (
                    principal_id TEXT NOT NULL,
                    window_key TEXT NOT NULL,
                    count INTEGER NOT NULL DEFAULT 1,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (principal_id, window_key)
                )
            """))

        await db.commit()
        self._initialized = True

    def _window_key(self, now_ms: int) -> str:
        """Generate window key for timestamp.

        Buckets time into window-sized chunks.
        """
        bucket = now_ms // self._window_ms
        return str(bucket)

    async def check(self, principal_id: str) -> RateDecision:
        """Check if request is allowed (without recording).

        Args:
            principal_id: Principal to check

        Returns:
            Rate decision
        """
        now_ms = int(time.time() * 1000)
        window_key = self._window_key(now_ms)

        async with self._db() as db:
            await self._ensure_table(db)

            result = await db.execute(
                text(f"""
                    SELECT count FROM {self._table_name}
                    WHERE principal_id = :principal_id AND window_key = :window_key
                """),
                {"principal_id": principal_id, "window_key": window_key},
            )
            row = result.fetchone()
            current_count = row[0] if row else 0

        allowed = current_count < self._max
        remaining = max(0, self._max - current_count)

        # Calculate retry_after
        retry_after_ms: int | None = None
        if not allowed:
            # Time until next window
            bucket_start = (now_ms // self._window_ms) * self._window_ms
            bucket_end = bucket_start + self._window_ms
            retry_after_ms = max(0, bucket_end - now_ms)

        return RateDecision(
            status=RateDecisionStatus.ALLOWED if allowed else RateDecisionStatus.LIMITED,
            allowed=allowed,
            remaining=remaining,
            limit=self._max,
            retry_after_ms=retry_after_ms,
            window_ms=self._window_ms,
        )

    async def record(self, principal_id: str) -> None:
        """Record request.

        Args:
            principal_id: Principal making request
        """
        now_ms = int(time.time() * 1000)
        window_key = self._window_key(now_ms)

        async with self._db() as db:
            await self._ensure_table(db)
            conn = await db.connection()
            dialect = conn.dialect.name

            if dialect == "postgresql":
                await db.execute(
                    text(f"""
                        INSERT INTO {self._table_name} (principal_id, window_key, count)
                        VALUES (:principal_id, :window_key, 1)
                        ON CONFLICT (principal_id, window_key)
                        DO UPDATE SET count = {self._table_name}.count + 1
                    """),
                    {"principal_id": principal_id, "window_key": window_key},
                )
            else:
                # SQLite UPSERT
                await db.execute(
                    text(f"""
                        INSERT INTO {self._table_name} (principal_id, window_key, count)
                        VALUES (:principal_id, :window_key, 1)
                        ON CONFLICT (principal_id, window_key)
                        DO UPDATE SET count = count + 1
                    """),
                    {"principal_id": principal_id, "window_key": window_key},
                )

            await db.commit()

    async def check_and_record(self, principal_id: str) -> RateDecision:
        """Check and record atomically.

        Uses Postgres RETURNING for atomic check-and-increment.

        Args:
            principal_id: Principal making request

        Returns:
            Rate decision
        """
        now_ms = int(time.time() * 1000)
        window_key = self._window_key(now_ms)

        async with self._db() as db:
            await self._ensure_table(db)
            conn = await db.connection()
            dialect = conn.dialect.name

            if dialect == "postgresql":
                # Atomic UPSERT with RETURNING
                result = await db.execute(
                    text(f"""
                        INSERT INTO {self._table_name} (principal_id, window_key, count)
                        VALUES (:principal_id, :window_key, 1)
                        ON CONFLICT (principal_id, window_key)
                        DO UPDATE SET count = {self._table_name}.count + 1
                        RETURNING count
                    """),
                    {"principal_id": principal_id, "window_key": window_key},
                )
                row = result.fetchone()
                new_count = row[0] if row else 1
            else:
                # SQLite: separate increment and select
                await db.execute(
                    text(f"""
                        INSERT INTO {self._table_name} (principal_id, window_key, count)
                        VALUES (:principal_id, :window_key, 1)
                        ON CONFLICT (principal_id, window_key)
                        DO UPDATE SET count = count + 1
                    """),
                    {"principal_id": principal_id, "window_key": window_key},
                )
                result = await db.execute(
                    text(f"""
                        SELECT count FROM {self._table_name}
                        WHERE principal_id = :principal_id AND window_key = :window_key
                    """),
                    {"principal_id": principal_id, "window_key": window_key},
                )
                row = result.fetchone()
                new_count = row[0] if row else 1

            await db.commit()

        # Check if over limit (already incremented)
        allowed = new_count <= self._max
        remaining = max(0, self._max - new_count)

        # Calculate retry_after
        retry_after_ms: int | None = None
        if not allowed:
            bucket_start = (now_ms // self._window_ms) * self._window_ms
            bucket_end = bucket_start + self._window_ms
            retry_after_ms = max(0, bucket_end - now_ms)

        return RateDecision(
            status=RateDecisionStatus.ALLOWED if allowed else RateDecisionStatus.LIMITED,
            allowed=allowed,
            remaining=remaining,
            limit=self._max,
            retry_after_ms=retry_after_ms,
            window_ms=self._window_ms,
        )

    async def cleanup_old_windows(self, *, keep_windows: int = 10) -> int:
        """Clean up old rate limit entries.

        Call periodically to prevent table bloat.

        Args:
            keep_windows: Number of recent windows to keep

        Returns:
            Number of rows deleted
        """
        now_ms = int(time.time() * 1000)
        current_bucket = now_ms // self._window_ms
        oldest_keep = str(current_bucket - keep_windows)

        async with self._db() as db:
            await self._ensure_table(db)

            result = await db.execute(
                text(f"""
                    DELETE FROM {self._table_name}
                    WHERE window_key < :oldest_keep
                """),
                {"oldest_keep": oldest_keep},
            )

            await db.commit()
            return result.rowcount or 0

    @property
    def max_per_window(self) -> int:
        """Maximum requests per window."""
        return self._max

    @property
    def window_ms(self) -> int:
        """Window duration in milliseconds."""
        return self._window_ms
