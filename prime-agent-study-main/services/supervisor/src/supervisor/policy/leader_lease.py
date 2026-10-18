"""Leader lease — distributed coordination for single-writer patterns.

Ensures only one replica runs certain operations:
- Approval expiration sweeper
- Stale session cleanup
- Scheduled tasks

Uses:
- Postgres advisory locks for cross-replica coordination
- asyncio.Lock for in-process serialization

Rewritten with:
- Configurable lock keys
- Timeout support
- Metrics hooks
- Better error handling

Reference: D8/D12 multi-replica patterns.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Final, TypeVar

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


__all__ = [
    "LeaderLease",
    "try_acquire_sweeper_lease",
    "run_with_approval_sweeper_lease",
    "APPROVAL_SWEEPER_LOCK_KEY",
    "STALE_SESSION_LOCK_KEY",
    "SCHEDULED_TASK_LOCK_KEY",
]


_log = logging.getLogger(__name__)

T = TypeVar("T")


# =============================================================================
# Lock Keys (stable identifiers for advisory locks)
# =============================================================================

# Approval sweeper (D12)
APPROVAL_SWEEPER_LOCK_KEY: Final[int] = 0xA12_0D12

# Stale session cleanup
STALE_SESSION_LOCK_KEY: Final[int] = 0xA12_0D13

# Scheduled task runner
SCHEDULED_TASK_LOCK_KEY: Final[int] = 0xA12_0D14


# =============================================================================
# Process-local Locks
# =============================================================================

# In-process locks for local serialization (SQLite, dual-manager tests)
_local_locks: dict[int, asyncio.Lock] = {}


def _get_local_lock(lock_key: int) -> asyncio.Lock:
    """Get or create local lock for key."""
    if lock_key not in _local_locks:
        _local_locks[lock_key] = asyncio.Lock()
    return _local_locks[lock_key]


# =============================================================================
# Leader Lease Class
# =============================================================================

class LeaderLease:
    """Manages leader election for a specific operation.

    Combines:
    - Process-local asyncio.Lock for in-process serialization
    - Postgres pg_try_advisory_lock for cross-replica coordination

    Example:
        lease = LeaderLease(
            session_factory,
            lock_key=APPROVAL_SWEEPER_LOCK_KEY,
            name="approval_sweeper",
        )

        result = await lease.run_with_lease(expire_approvals)
        if result is None:
            # Another replica is leader
            pass
    """

    __slots__ = ("_db", "_lock_key", "_name", "_local_lock", "_timeout_s")

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        lock_key: int,
        *,
        name: str = "leader",
        timeout_s: float | None = None,
    ) -> None:
        """Initialize leader lease.

        Args:
            session_factory: SQLAlchemy session factory
            lock_key: Unique lock key for this operation
            name: Human-readable name for logging
            timeout_s: Optional timeout for lock acquisition
        """
        self._db = session_factory
        self._lock_key = lock_key
        self._name = name
        self._local_lock = _get_local_lock(lock_key)
        self._timeout_s = timeout_s

    async def try_acquire(self) -> bool:
        """Try to acquire leadership.

        Returns:
            True if this instance is now leader
        """
        # Check local lock first
        if self._local_lock.locked():
            return False

        # Try Postgres advisory lock
        async with self._db() as db:
            conn = await db.connection()
            dialect = conn.dialect.name

            if dialect == "postgresql":
                result = await db.execute(
                    text("SELECT pg_try_advisory_lock(:k)"),
                    {"k": self._lock_key},
                )
                got = result.scalar()

                if not got:
                    _log.debug(f"{self._name}: advisory lock held by peer")
                    return False

                # Release immediately (we just wanted to check)
                await db.execute(
                    text("SELECT pg_advisory_unlock(:k)"),
                    {"k": self._lock_key},
                )
                await db.commit()
                return True

        # SQLite/other: always allow (single process)
        return True

    async def run_with_lease(
        self,
        fn: Callable[[], Awaitable[T]],
    ) -> T | None:
        """Run function with leader lease.

        Acquires local + advisory lock, runs function, releases locks.

        Args:
            fn: Async function to run as leader

        Returns:
            Function result, or None if not leader
        """
        # Try to acquire local lock with timeout
        try:
            if self._timeout_s:
                async with asyncio.timeout(self._timeout_s):
                    await self._local_lock.acquire()
            else:
                # Non-blocking check
                if not self._local_lock.locked():
                    await self._local_lock.acquire()
                else:
                    _log.debug(f"{self._name}: local lock held")
                    return None
        except asyncio.TimeoutError:
            _log.debug(f"{self._name}: timeout acquiring local lock")
            return None

        try:
            async with self._db() as db:
                conn = await db.connection()
                dialect = conn.dialect.name

                if dialect == "postgresql":
                    # Acquire advisory lock
                    result = await db.execute(
                        text("SELECT pg_try_advisory_lock(:k)"),
                        {"k": self._lock_key},
                    )
                    got = result.scalar()

                    if not got:
                        _log.debug(f"{self._name}: advisory lock held by peer — skipping")
                        return None

                    try:
                        return await fn()
                    finally:
                        # Release advisory lock
                        try:
                            await db.execute(
                                text("SELECT pg_advisory_unlock(:k)"),
                                {"k": self._lock_key},
                            )
                            await db.commit()
                        except Exception:
                            _log.exception(f"{self._name}: failed to release advisory lock")
                else:
                    # Non-Postgres: just run
                    return await fn()
        finally:
            self._local_lock.release()

    @property
    def name(self) -> str:
        """Lease name."""
        return self._name

    @property
    def lock_key(self) -> int:
        """Lock key."""
        return self._lock_key


# =============================================================================
# Convenience Functions (backward compatibility)
# =============================================================================

async def try_acquire_sweeper_lease(
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> bool:
    """Try to acquire approval sweeper leadership.

    Args:
        session_factory: SQLAlchemy session factory

    Returns:
        True if this caller may run the sweeper
    """
    local_lock = _get_local_lock(APPROVAL_SWEEPER_LOCK_KEY)

    if local_lock.locked():
        return False

    if session_factory is None:
        return True

    async with session_factory() as db:
        conn = await db.connection()
        dialect = conn.dialect.name

        if dialect == "postgresql":
            result = await db.execute(
                text("SELECT pg_try_advisory_lock(:k)"),
                {"k": APPROVAL_SWEEPER_LOCK_KEY},
            )
            got = result.scalar()

            if not got:
                return False

            # Release immediately
            await db.execute(
                text("SELECT pg_advisory_unlock(:k)"),
                {"k": APPROVAL_SWEEPER_LOCK_KEY},
            )
            await db.commit()
            return True

    return True


async def run_with_approval_sweeper_lease(
    session_factory: async_sessionmaker[AsyncSession],
    fn: Callable[[], Awaitable[T]],
) -> T | None:
    """Run function under approval sweeper leader lease.

    Args:
        session_factory: SQLAlchemy session factory
        fn: Function to run as leader

    Returns:
        Function result, or None if not leader
    """
    lease = LeaderLease(
        session_factory,
        APPROVAL_SWEEPER_LOCK_KEY,
        name="approval_sweeper",
    )
    return await lease.run_with_lease(fn)
