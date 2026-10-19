"""Route storage implementations.

This module provides concrete RouteStore implementations:
- MemoryRouteStore: In-memory for testing
- PostgresRouteStore: Postgres-backed with 15-second caching

The caching strategy follows QM's pattern:
- Routes are cached for 15 seconds to reduce database load
- Cache is invalidated on writes
- Version tracking enables cluster-wide invalidation

Reference: QM's DurableMap pattern with ROUTE_CACHE_TTL_MS = 15_000
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, ClassVar

from supervisor.sandbox.routing.routes import SandboxRoute

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

__all__ = [
    "MemoryRouteStore",
    "PostgresRouteStore",
]


def _utcnow() -> datetime:
    """Return current UTC time with timezone info."""
    return datetime.now(UTC)


@dataclass
class MemoryRouteStore:
    """In-memory route store for testing.

    Simple dict-based storage without caching (not needed in-memory).

    Example:
        >>> store = MemoryRouteStore()
        >>> await store.put(SandboxRoute(scope_id="x", backend="e2b"))
        >>> route = await store.get("x")
        >>> route.backend
        'e2b'
    """

    _routes: dict[str, SandboxRoute] = field(default_factory=dict)
    _version: int = 0

    async def get(self, scope_id: str) -> SandboxRoute | None:
        """Get route for scope."""
        return self._routes.get(scope_id)

    async def put(self, route: SandboxRoute) -> None:
        """Create or replace route."""
        self._routes[route.scope_id] = route
        self._version += 1

    async def merge(
        self,
        scope_id: str,
        **updates: Any,
    ) -> SandboxRoute | None:
        """Partial update of existing route."""
        existing = self._routes.get(scope_id)
        if existing is None:
            return None

        data = existing.to_dict()
        data.update(updates)
        updated = SandboxRoute.from_dict(data)
        self._routes[scope_id] = updated
        self._version += 1
        return updated

    async def delete(self, scope_id: str) -> bool:
        """Delete route."""
        if scope_id in self._routes:
            del self._routes[scope_id]
            self._version += 1
            return True
        return False

    async def list_all(self) -> list[SandboxRoute]:
        """List all routes."""
        return list(self._routes.values())

    async def list_by_backend(self, backend: str) -> list[SandboxRoute]:
        """List routes using specific backend."""
        return [r for r in self._routes.values() if r.backend == backend]

    def clear(self) -> None:
        """Clear all routes. For testing only."""
        self._routes.clear()
        self._version = 0


class PostgresRouteStore:
    """Postgres-backed route store with caching.

    Features:
    - 15-second TTL cache per scope (QM's ROUTE_CACHE_TTL_MS)
    - Write-through cache invalidation
    - Upsert semantics for put()

    Example:
        >>> store = PostgresRouteStore(session_factory)
        >>> await store.put(SandboxRoute(scope_id="x", backend="e2b"))
        >>> route = await store.get("x")  # Cached for 15s
    """

    CACHE_TTL_SECONDS: ClassVar[float] = 15.0

    __slots__ = ("_cache", "_db", "_version")

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        """Initialize with SQLAlchemy async session factory.

        Args:
            session_factory: Factory for creating async database sessions.
        """
        self._db = session_factory
        self._cache: dict[str, tuple[SandboxRoute, float]] = {}
        self._version: int = 0

    def _is_cache_valid(self, scope_id: str) -> bool:
        """Check if cached route is still valid."""
        if scope_id not in self._cache:
            return False
        _, cached_at = self._cache[scope_id]
        return (time.monotonic() - cached_at) < self.CACHE_TTL_SECONDS

    def _cache_route(self, route: SandboxRoute) -> None:
        """Add route to cache."""
        self._cache[route.scope_id] = (route, time.monotonic())

    def _invalidate(self, scope_id: str) -> None:
        """Remove route from cache."""
        self._cache.pop(scope_id, None)
        self._version += 1

    def _row_to_route(self, row: Any) -> SandboxRoute:
        """Convert database row to SandboxRoute."""
        capabilities_lost = row.capabilities_lost or []
        if isinstance(capabilities_lost, list):
            capabilities_lost = tuple(capabilities_lost)

        return SandboxRoute(
            scope_id=row.scope_id,
            backend=row.backend,
            image_digest=row.image_digest,
            priority=row.priority,
            pinned=row.pinned,
            reason=row.reason,
            migrated_at=row.migrated_at,
            migrated_from=row.migrated_from,
            migration_sha=row.migration_sha,
            capabilities_lost=capabilities_lost,
            created_at=row.created_at,
        )

    async def get(self, scope_id: str) -> SandboxRoute | None:
        """Get route for scope, with caching."""
        if self._is_cache_valid(scope_id):
            route, _ = self._cache[scope_id]
            return route

        from sqlalchemy import select

        from supervisor.db.models import SandboxRouteRow

        async with self._db() as db:
            row = await db.scalar(
                select(SandboxRouteRow).where(SandboxRouteRow.scope_id == scope_id)
            )
            if row is None:
                return None

            route = self._row_to_route(row)
            self._cache_route(route)
            return route

    async def put(self, route: SandboxRoute) -> None:
        """Create or replace route (upsert)."""
        from sqlalchemy import select

        from supervisor.db.models import SandboxRouteRow

        async with self._db() as db:
            existing = await db.scalar(
                select(SandboxRouteRow).where(
                    SandboxRouteRow.scope_id == route.scope_id
                )
            )

            if existing:
                existing.backend = route.backend
                existing.image_digest = route.image_digest
                existing.priority = route.priority
                existing.pinned = route.pinned
                existing.reason = route.reason
                existing.migrated_at = route.migrated_at
                existing.migrated_from = route.migrated_from
                existing.migration_sha = route.migration_sha
                existing.capabilities_lost = list(route.capabilities_lost)
            else:
                db.add(SandboxRouteRow(
                    scope_id=route.scope_id,
                    backend=route.backend,
                    image_digest=route.image_digest,
                    priority=route.priority,
                    pinned=route.pinned,
                    reason=route.reason,
                    migrated_at=route.migrated_at,
                    migrated_from=route.migrated_from,
                    migration_sha=route.migration_sha,
                    capabilities_lost=list(route.capabilities_lost),
                    created_at=route.created_at,
                ))

            await db.commit()

        self._invalidate(route.scope_id)
        self._cache_route(route)

    async def merge(
        self,
        scope_id: str,
        **updates: Any,
    ) -> SandboxRoute | None:
        """Partial update of existing route."""
        from sqlalchemy import select

        from supervisor.db.models import SandboxRouteRow

        async with self._db() as db:
            row = await db.scalar(
                select(SandboxRouteRow).where(SandboxRouteRow.scope_id == scope_id)
            )
            if row is None:
                return None

            for key, value in updates.items():
                if hasattr(row, key):
                    if key == "capabilities_lost" and isinstance(value, tuple):
                        value = list(value)
                    setattr(row, key, value)

            await db.commit()

            route = self._row_to_route(row)
            self._invalidate(scope_id)
            self._cache_route(route)
            return route

    async def delete(self, scope_id: str) -> bool:
        """Delete route."""
        from sqlalchemy import delete

        from supervisor.db.models import SandboxRouteRow

        async with self._db() as db:
            result = await db.execute(
                delete(SandboxRouteRow).where(SandboxRouteRow.scope_id == scope_id)
            )
            await db.commit()

            deleted = result.rowcount > 0

        if deleted:
            self._invalidate(scope_id)

        return deleted

    async def list_all(self) -> list[SandboxRoute]:
        """List all routes."""
        from sqlalchemy import select

        from supervisor.db.models import SandboxRouteRow

        async with self._db() as db:
            result = await db.execute(select(SandboxRouteRow))
            rows = result.scalars().all()
            return [self._row_to_route(row) for row in rows]

    async def list_by_backend(self, backend: str) -> list[SandboxRoute]:
        """List routes using specific backend."""
        from sqlalchemy import select

        from supervisor.db.models import SandboxRouteRow

        async with self._db() as db:
            result = await db.execute(
                select(SandboxRouteRow).where(SandboxRouteRow.backend == backend)
            )
            rows = result.scalars().all()
            return [self._row_to_route(row) for row in rows]

    def clear_cache(self) -> None:
        """Clear the in-memory cache. For testing only."""
        self._cache.clear()
