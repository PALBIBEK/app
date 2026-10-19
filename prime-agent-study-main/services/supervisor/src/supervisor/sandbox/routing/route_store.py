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

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from supervisor.sandbox.routing.routes import SandboxRoute

if TYPE_CHECKING:
    pass

__all__ = [
    "MemoryRouteStore",
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


