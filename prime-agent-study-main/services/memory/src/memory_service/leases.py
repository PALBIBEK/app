"""Fenced session leases — multi-pod writer safety."""

from __future__ import annotations

import time

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from memory_service.models import MemorySessionRow
from memory_service.types import LeaseHeld, SessionNotFound, StaleFence


class LeaseManager:
    def __init__(
        self,
        factory: async_sessionmaker[AsyncSession],
        *,
        ttl_ms: int = 30_000,
    ) -> None:
        self._factory = factory
        self._ttl_ms = ttl_ms

    async def acquire(self, session_id: str, owner: str) -> int:
        """Acquire or renew lease. Returns fence token. Raises LeaseHeld if another owner."""
        now = int(time.time() * 1000)
        async with self._factory() as db:
            async with db.begin():
                row = await db.get(MemorySessionRow, session_id, with_for_update=True)
                if row is None:
                    raise SessionNotFound(f"SESSION_NOT_FOUND: {session_id}")
                expired = row.lease_until_ms is None or row.lease_until_ms < now
                same = row.lease_owner == owner
                if not expired and not same:
                    raise LeaseHeld(
                        f"LEASE_HELD: owner={row.lease_owner}",
                        code="LEASE_HELD",
                    )
                row.lease_owner = owner
                row.lease_until_ms = now + self._ttl_ms
                row.fence = int(row.fence) + 1
                return int(row.fence)

    async def assert_fence(self, session_id: str, fence: int | None) -> None:
        """Reject stale writers. fence=None allowed only when no lease held/expired."""
        now = int(time.time() * 1000)
        async with self._factory() as db:
            row = await db.get(MemorySessionRow, session_id)
            if row is None:
                raise SessionNotFound(f"SESSION_NOT_FOUND: {session_id}")
            active = (
                row.lease_owner is not None
                and row.lease_until_ms is not None
                and row.lease_until_ms >= now
            )
            if not active:
                return
            if fence is None or int(fence) != int(row.fence):
                raise StaleFence(
                    f"STALE_FENCE: expected={row.fence} got={fence}",
                    code="STALE_FENCE",
                )
