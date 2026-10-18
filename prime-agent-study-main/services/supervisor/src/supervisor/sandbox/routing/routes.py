"""Sandbox route types and storage protocol.

This module defines the core types for dynamic sandbox routing:
- SandboxRoute: Persistent route configuration for a scope
- RouteStore: Protocol for route storage backends

Routes map scope IDs to backend providers, enabling:
- Per-scope backend selection (e.g., "customer-123" → "e2b")
- Migration tracking (when/why a scope changed backends)
- Capability loss tracking (what features were lost on migration)
- Route pinning (prevent accidental migrations)

Reference: QM's sandbox-routing.ts pattern with 15-second caching.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Protocol, runtime_checkable

__all__ = [
    "SandboxRoute",
    "RouteStore",
    "RouteNotFoundError",
]


class RouteNotFoundError(KeyError):
    """Route does not exist for scope."""

    def __init__(self, scope_id: str) -> None:
        super().__init__(f"no route for scope {scope_id!r}")
        self.scope_id = scope_id


def _utcnow() -> datetime:
    """Return current UTC time with timezone info."""
    return datetime.now(timezone.utc)


@dataclass(frozen=True, slots=True)
class SandboxRoute:
    """Persistent route configuration for a scope.

    Attributes:
        scope_id: Unique identifier for the scope (e.g., "customer-123").
        backend: Provider name to use (e.g., "e2b", "local_docker").
        image_digest: Optional image override (takes precedence over manifest).
        priority: Route priority for fallback ordering (higher = tried first).
        pinned: If True, prevents automatic migrations.
        reason: Human-readable reason for pinning or last migration.
        migrated_at: Timestamp of last migration.
        migrated_from: Previous backend before migration.
        migration_sha: SHA256 of workspace contents at migration time.
        capabilities_lost: Capabilities lost during last migration.
        created_at: When this route was created.

    Example:
        >>> route = SandboxRoute(
        ...     scope_id="customer-123",
        ...     backend="e2b",
        ...     pinned=True,
        ...     reason="production customer",
        ... )
    """

    scope_id: str
    backend: str
    image_digest: str | None = None
    priority: int = 0
    pinned: bool = False
    reason: str | None = None
    migrated_at: datetime | None = None
    migrated_from: str | None = None
    migration_sha: str | None = None
    capabilities_lost: tuple[str, ...] = ()
    created_at: datetime = field(default_factory=_utcnow)

    def with_migration(
        self,
        *,
        to_backend: str,
        migration_sha: str | None = None,
        capabilities_lost: tuple[str, ...] = (),
        reason: str | None = None,
    ) -> SandboxRoute:
        """Create new route with migration metadata.

        Args:
            to_backend: New backend after migration.
            migration_sha: SHA256 of migrated workspace.
            capabilities_lost: Capabilities lost in migration.
            reason: Why the migration happened.

        Returns:
            New SandboxRoute with updated migration fields.
        """
        return SandboxRoute(
            scope_id=self.scope_id,
            backend=to_backend,
            image_digest=self.image_digest,
            priority=self.priority,
            pinned=self.pinned,
            reason=reason or self.reason,
            migrated_at=_utcnow(),
            migrated_from=self.backend,
            migration_sha=migration_sha,
            capabilities_lost=capabilities_lost,
            created_at=self.created_at,
        )

    def with_pin(self, *, pinned: bool, reason: str | None = None) -> SandboxRoute:
        """Create new route with updated pin status.

        Args:
            pinned: New pin status.
            reason: Why pinned/unpinned.

        Returns:
            New SandboxRoute with updated pin fields.
        """
        return SandboxRoute(
            scope_id=self.scope_id,
            backend=self.backend,
            image_digest=self.image_digest,
            priority=self.priority,
            pinned=pinned,
            reason=reason or self.reason,
            migrated_at=self.migrated_at,
            migrated_from=self.migrated_from,
            migration_sha=self.migration_sha,
            capabilities_lost=self.capabilities_lost,
            created_at=self.created_at,
        )

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization.

        Returns:
            Dict with all fields, datetime as ISO string.
        """
        return {
            "scope_id": self.scope_id,
            "backend": self.backend,
            "image_digest": self.image_digest,
            "priority": self.priority,
            "pinned": self.pinned,
            "reason": self.reason,
            "migrated_at": self.migrated_at.isoformat() if self.migrated_at else None,
            "migrated_from": self.migrated_from,
            "migration_sha": self.migration_sha,
            "capabilities_lost": list(self.capabilities_lost),
            "created_at": self.created_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SandboxRoute:
        """Create from dictionary.

        Args:
            data: Dict with route fields.

        Returns:
            SandboxRoute instance.
        """
        migrated_at = data.get("migrated_at")
        if isinstance(migrated_at, str):
            migrated_at = datetime.fromisoformat(migrated_at)

        created_at = data.get("created_at")
        if isinstance(created_at, str):
            created_at = datetime.fromisoformat(created_at)
        elif created_at is None:
            created_at = _utcnow()

        capabilities_lost = data.get("capabilities_lost", ())
        if isinstance(capabilities_lost, list):
            capabilities_lost = tuple(capabilities_lost)

        return cls(
            scope_id=data["scope_id"],
            backend=data["backend"],
            image_digest=data.get("image_digest"),
            priority=data.get("priority", 0),
            pinned=data.get("pinned", False),
            reason=data.get("reason"),
            migrated_at=migrated_at,
            migrated_from=data.get("migrated_from"),
            migration_sha=data.get("migration_sha"),
            capabilities_lost=capabilities_lost,
            created_at=created_at,
        )


@runtime_checkable
class RouteStore(Protocol):
    """Abstract storage for sandbox routes.

    Implementations:
        - MemoryRouteStore: In-memory for testing
        - PostgresRouteStore: Postgres-backed with caching

    All methods are async to support database operations.

    Example:
        >>> store = MemoryRouteStore()
        >>> await store.put(SandboxRoute(scope_id="x", backend="e2b"))
        >>> route = await store.get("x")
        >>> route.backend
        'e2b'
    """

    async def get(self, scope_id: str) -> SandboxRoute | None:
        """Get route for scope.

        Args:
            scope_id: Scope to look up.

        Returns:
            SandboxRoute if found, None if not routed.
        """
        ...

    async def put(self, route: SandboxRoute) -> None:
        """Create or replace route.

        Args:
            route: Route to store.
        """
        ...

    async def merge(
        self,
        scope_id: str,
        **updates: Any,
    ) -> SandboxRoute | None:
        """Partial update of existing route.

        Args:
            scope_id: Scope to update.
            **updates: Fields to update.

        Returns:
            Updated route, or None if scope not found.
        """
        ...

    async def delete(self, scope_id: str) -> bool:
        """Delete route.

        Args:
            scope_id: Scope to delete.

        Returns:
            True if route existed and was deleted.
        """
        ...

    async def list_all(self) -> list[SandboxRoute]:
        """List all routes.

        Returns:
            All routes in storage (for admin/migration tools).
        """
        ...

    async def list_by_backend(self, backend: str) -> list[SandboxRoute]:
        """List routes using specific backend.

        Args:
            backend: Backend name to filter by.

        Returns:
            Routes using the specified backend.
        """
        ...
