"""Advisory locking for serializing sandbox operations.

This module provides advisory locks for serializing operations like migrations
that must not run concurrently for the same scope.

Implementations:
- AdvisoryLock: Postgres pg_advisory_lock (production)
- MemoryAdvisoryLock: In-memory asyncio locks (testing)

Reference: QM uses advisory locks to prevent concurrent migrations.
"""

from __future__ import annotations

import asyncio
import hashlib
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, AsyncIterator, Protocol, runtime_checkable

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

__all__ = [
    "AdvisoryLockProtocol",
    "AdvisoryLock",
    "MemoryAdvisoryLock",
]


@runtime_checkable
class AdvisoryLockProtocol(Protocol):
    """Protocol for advisory lock implementations."""

    @asynccontextmanager
    async def acquire(self, key: str) -> AsyncIterator[None]:
        """Acquire advisory lock for key (blocking).

        Args:
            key: Lock identifier string.

        Yields:
            None when lock is acquired.

        Example:
            >>> async with lock.acquire("sandbox-migration:scope-123"):
            ...     # Protected operation
            ...     await do_migration()
        """
        ...

    async def try_acquire(self, key: str) -> bool:
        """Try to acquire lock without blocking.

        Args:
            key: Lock identifier string.

        Returns:
            True if acquired, False if already held by another session.
        """
        ...


class AdvisoryLock:
    """Postgres advisory lock for serializing operations.

    Uses pg_advisory_lock which:
    - Is session-scoped (automatically released on disconnect)
    - Is re-entrant (same session can acquire same lock multiple times)
    - Blocks until lock is available

    The lock ID is derived from a SHA256 hash of the key string,
    converted to a signed 64-bit integer.

    Example:
        >>> lock = AdvisoryLock(session_factory)
        >>> async with lock.acquire("sandbox-migration:scope-123"):
        ...     await migrate_scope("scope-123")
    """

    __slots__ = ("_db",)

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        """Initialize with SQLAlchemy async session factory.

        Args:
            session_factory: Factory for creating async database sessions.
        """
        self._db = session_factory

    @staticmethod
    def _key_to_id(key: str) -> int:
        """Convert string key to 64-bit lock ID.

        Uses first 8 bytes of SHA256 as signed int64.
        This ensures consistent lock IDs across processes.

        Args:
            key: Lock identifier string.

        Returns:
            Signed 64-bit integer lock ID.
        """
        h = hashlib.sha256(key.encode("utf-8")).digest()[:8]
        return int.from_bytes(h, byteorder="big", signed=True)

    @asynccontextmanager
    async def acquire(self, key: str) -> AsyncIterator[None]:
        """Acquire advisory lock for key (blocking).

        Blocks until the lock is available. The lock is automatically
        released when exiting the context manager.

        Args:
            key: Lock identifier string.

        Yields:
            None when lock is acquired.
        """
        from sqlalchemy import text

        lock_id = self._key_to_id(key)

        async with self._db() as db:
            await db.execute(text(f"SELECT pg_advisory_lock({lock_id})"))
            try:
                yield
            finally:
                await db.execute(text(f"SELECT pg_advisory_unlock({lock_id})"))

    async def try_acquire(self, key: str) -> bool:
        """Try to acquire lock without blocking.

        Args:
            key: Lock identifier string.

        Returns:
            True if lock was acquired, False if already held.
        """
        from sqlalchemy import text

        lock_id = self._key_to_id(key)

        async with self._db() as db:
            result = await db.execute(
                text(f"SELECT pg_try_advisory_lock({lock_id})")
            )
            return bool(result.scalar_one())

    async def is_locked(self, key: str) -> bool:
        """Check if a key is currently locked (by any session).

        Note: This is racy - the lock state may change immediately
        after this call returns.

        Args:
            key: Lock identifier string.

        Returns:
            True if currently locked.
        """
        from sqlalchemy import text

        lock_id = self._key_to_id(key)

        async with self._db() as db:
            acquired = await db.execute(
                text(f"SELECT pg_try_advisory_lock({lock_id})")
            )
            if acquired.scalar_one():
                await db.execute(text(f"SELECT pg_advisory_unlock({lock_id})"))
                return False
            return True


class MemoryAdvisoryLock:
    """In-memory advisory lock for testing.

    Uses asyncio.Condition for coordination between tasks.
    Does not persist across process restarts.

    Example:
        >>> lock = MemoryAdvisoryLock()
        >>> async with lock.acquire("test-key"):
        ...     await do_work()
    """

    __slots__ = ("_locks", "_condition")

    def __init__(self) -> None:
        """Initialize empty lock set."""
        self._locks: set[str] = set()
        self._condition: asyncio.Condition | None = None

    def _get_condition(self) -> asyncio.Condition:
        """Get or create condition variable.

        Creates the condition lazily to handle event loop changes.
        """
        if self._condition is None:
            self._condition = asyncio.Condition()
        return self._condition

    @asynccontextmanager
    async def acquire(self, key: str) -> AsyncIterator[None]:
        """Acquire lock for key (blocking).

        Waits until the key is not held by another task.

        Args:
            key: Lock identifier string.

        Yields:
            None when lock is acquired.
        """
        condition = self._get_condition()

        async with condition:
            while key in self._locks:
                await condition.wait()
            self._locks.add(key)

        try:
            yield
        finally:
            async with condition:
                self._locks.discard(key)
                condition.notify_all()

    async def try_acquire(self, key: str) -> bool:
        """Try to acquire lock without blocking.

        Args:
            key: Lock identifier string.

        Returns:
            True if acquired, False if already held.
        """
        condition = self._get_condition()

        async with condition:
            if key in self._locks:
                return False
            self._locks.add(key)
            return True

    async def release(self, key: str) -> bool:
        """Release a lock acquired with try_acquire.

        Args:
            key: Lock identifier string.

        Returns:
            True if lock was held and released.
        """
        condition = self._get_condition()

        async with condition:
            if key not in self._locks:
                return False
            self._locks.discard(key)
            condition.notify_all()
            return True

    async def is_locked(self, key: str) -> bool:
        """Check if key is currently locked.

        Args:
            key: Lock identifier string.

        Returns:
            True if currently locked.
        """
        return key in self._locks

    def clear(self) -> None:
        """Clear all locks. For testing only."""
        self._locks.clear()
