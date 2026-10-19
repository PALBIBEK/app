"""Advisory locking for serializing sandbox operations.

This module provides advisory locks for serializing operations like migrations
that must not run concurrently for the same scope.

Implementations:
- AdvisoryLock: Postgres pg_advisory_lock (production)
- MemoryAdvisoryLock: In-memory asyncio locks (testing)

Reference: QM uses advisory locks to prevent concurrent migrations.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Protocol, runtime_checkable

__all__ = [
    "AdvisoryLockProtocol",
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


