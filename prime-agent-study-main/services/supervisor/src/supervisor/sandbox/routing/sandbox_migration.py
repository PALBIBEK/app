"""Sandbox migration between backends.

This module handles migrating scopes from one backend to another:
- Validates migration safety (pinned? live work? capabilities?)
- Copies workspace from source to destination
- Updates route in database
- Optionally resyncs if source changed during copy

Migration flow:
1. Validate: Check pinned, live work, capability loss
2. Lock: Acquire advisory lock for scope
3. Copy: Transfer workspace via sync or tar
4. Route: Update database route
5. (Optional) Resync: Re-copy if source changed

Reference: QM's sandbox-migration-runner.ts pattern.
"""

from __future__ import annotations

import hashlib
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from supervisor.sandbox.base import Sandbox, SandboxProvider
from supervisor.sandbox.routing.capabilities import (
    PROVIDER_CAPABILITIES,
    capabilities_lost_moving_to,
)
from supervisor.sandbox.routing.routes import RouteStore, SandboxRoute

if TYPE_CHECKING:
    from supervisor.sandbox.routing.advisory_lock import AdvisoryLockProtocol

__all__ = [
    "MigrationBlockedError",
    "MigrationResult",
    "SandboxMigrationRunner",
]


class MigrationBlockedError(Exception):
    """Migration cannot proceed."""

    def __init__(
        self,
        reason: str,
        *,
        scope_id: str,
        to_backend: str,
    ) -> None:
        super().__init__(f"migration blocked for {scope_id}: {reason}")
        self.reason = reason
        self.scope_id = scope_id
        self.to_backend = to_backend


@dataclass(frozen=True, slots=True)
class MigrationResult:
    """Result of a scope migration."""

    success: bool
    scope_id: str
    from_backend: str
    to_backend: str
    migration_sha: str | None = None
    capabilities_lost: tuple[str, ...] = ()
    error: str | None = None
    duration_ms: int = 0

    def to_dict(self) -> dict:
        """Convert to dict for serialization."""
        return {
            "success": self.success,
            "scope_id": self.scope_id,
            "from_backend": self.from_backend,
            "to_backend": self.to_backend,
            "migration_sha": self.migration_sha,
            "capabilities_lost": list(self.capabilities_lost),
            "error": self.error,
            "duration_ms": self.duration_ms,
        }


LiveWorkChecker = Callable[[str], Awaitable[bool]]


class SandboxMigrationRunner:
    """Handles sandbox migrations between backends.

    Migration flow:
    1. Validate (pinned? live work? capabilities?)
    2. Copy workspace from source to destination
    3. Update route in database
    4. Optionally resync if source changed during copy

    Example:
        >>> runner = SandboxMigrationRunner(
        ...     backends=backends,
        ...     routes=route_store,
        ...     default_backend="e2b",
        ... )
        >>> result = await runner.migrate_scope("scope-123", "local_docker")
        >>> result.success
        True
    """

    __slots__ = (
        "_backends",
        "_default",
        "_has_live_work",
        "_lock",
        "_routes",
        "_settle_ms",
    )

    def __init__(
        self,
        *,
        backends: dict[str, SandboxProvider],
        routes: RouteStore,
        default_backend: str,
        advisory_lock: AdvisoryLockProtocol | None = None,
        settle_ms: int = 15_000,
        has_live_work: LiveWorkChecker | None = None,
    ) -> None:
        """Initialize migration runner.

        Args:
            backends: Map of backend name to provider.
            routes: Route storage backend.
            default_backend: Default backend for unrouted scopes.
            advisory_lock: Lock for serializing migrations.
            settle_ms: Time to wait before resync check (default 15s).
            has_live_work: Callback to check for live background work.
        """
        self._backends = backends
        self._routes = routes
        self._default = default_backend
        self._lock = advisory_lock
        self._settle_ms = settle_ms
        self._has_live_work = has_live_work

    @property
    def available_backends(self) -> list[str]:
        """List available backend names."""
        return list(self._backends.keys())

    @property
    def default_backend(self) -> str:
        """Default backend for unrouted scopes."""
        return self._default

    async def list_routes(self) -> list[tuple[str, SandboxRoute]]:
        """List all routes with scope IDs."""
        routes = await self._routes.list_all()
        return [(r.scope_id, r) for r in routes]

    async def migrate_scope(
        self,
        scope_id: str,
        to_backend: str,
        *,
        reason: str | None = None,
        force: bool = False,
    ) -> MigrationResult:
        """Migrate scope to new backend.

        Args:
            scope_id: Scope to migrate.
            to_backend: Target backend.
            reason: Why migrating (for audit).
            force: Override pinned/capability checks.

        Returns:
            MigrationResult with outcome.

        Raises:
            MigrationBlockedError: If migration cannot proceed.
        """
        start_time = time.monotonic()

        route = await self._routes.get(scope_id)
        from_backend = route.backend if route else self._default

        if from_backend == to_backend:
            return MigrationResult(
                success=True,
                scope_id=scope_id,
                from_backend=from_backend,
                to_backend=to_backend,
                duration_ms=0,
            )

        if route and route.pinned and not force:
            raise MigrationBlockedError(
                "route is pinned",
                scope_id=scope_id,
                to_backend=to_backend,
            )

        if to_backend not in self._backends:
            raise MigrationBlockedError(
                f"backend {to_backend!r} not available",
                scope_id=scope_id,
                to_backend=to_backend,
            )

        if self._has_live_work is not None:
            has_work = await self._has_live_work(scope_id)
            if has_work:
                raise MigrationBlockedError(
                    "scope has live background work",
                    scope_id=scope_id,
                    to_backend=to_backend,
                )

        from_caps = PROVIDER_CAPABILITIES.get(from_backend, frozenset())
        to_caps = PROVIDER_CAPABILITIES.get(to_backend, frozenset())
        lost = capabilities_lost_moving_to(from_caps, to_caps)

        if lost and not force:
            raise MigrationBlockedError(
                f"would lose capabilities: {', '.join(lost)}",
                scope_id=scope_id,
                to_backend=to_backend,
            )

        lock_key = f"sandbox-migration:{scope_id}"

        async with self._maybe_lock(lock_key):
            migration_sha = await self._copy_workspace(
                scope_id,
                from_backend,
                to_backend,
            )

            new_route = SandboxRoute(
                scope_id=scope_id,
                backend=to_backend,
                image_digest=route.image_digest if route else None,
                priority=route.priority if route else 0,
                pinned=route.pinned if route else False,
                migrated_at=datetime.now(UTC),
                migrated_from=from_backend,
                migration_sha=migration_sha,
                capabilities_lost=lost,
                reason=reason,
            )
            await self._routes.put(new_route)

        duration_ms = int((time.monotonic() - start_time) * 1000)

        return MigrationResult(
            success=True,
            scope_id=scope_id,
            from_backend=from_backend,
            to_backend=to_backend,
            migration_sha=migration_sha,
            capabilities_lost=lost,
            duration_ms=duration_ms,
        )

    async def pin_route(
        self,
        scope_id: str,
        *,
        pinned: bool = True,
        reason: str | None = None,
    ) -> SandboxRoute | None:
        """Pin or unpin a route.

        Pinned routes cannot be automatically migrated.

        Args:
            scope_id: Scope to pin.
            pinned: Pin status.
            reason: Why pinned.

        Returns:
            Updated route, or None if not found.
        """
        route = await self._routes.get(scope_id)
        if route is None:
            return None

        updated = route.with_pin(pinned=pinned, reason=reason)
        await self._routes.put(updated)
        return updated

    async def _copy_workspace(
        self,
        scope_id: str,
        from_backend: str,
        to_backend: str,
    ) -> str | None:
        """Copy workspace from source backend to destination.

        Flow:
        1. Create temporary sandboxes for the copy
        2. Sync source workspace to durable store (if supported)
        3. Hydrate destination from durable store (if supported)
        4. Or fall back to tar-based copy

        Args:
            scope_id: Scope being migrated.
            from_backend: Source backend name.
            to_backend: Destination backend name.

        Returns:
            SHA256 of workspace contents, or None if no workspace.
        """
        from_provider = self._backends.get(from_backend)
        to_provider = self._backends.get(to_backend)

        if from_provider is None or to_provider is None:
            return None

        from_sandbox = await from_provider.create(
            labels={"scope_id": scope_id, "purpose": "migration_source"},
        )
        to_sandbox = await to_provider.create(
            labels={"scope_id": scope_id, "purpose": "migration_dest"},
        )

        try:
            if not from_sandbox.info.supports_workspace_sync:
                return await self._copy_workspace_via_tar(
                    scope_id, from_sandbox, to_sandbox
                )

            sync_report = await from_sandbox.sync_workspace(scope_id)

            if to_sandbox.info.supports_workspace_sync:
                await to_sandbox.hydrate_workspace(scope_id)

            migration_sha = getattr(sync_report, "content_hash", None)
            return migration_sha

        finally:
            await from_sandbox.destroy()
            await to_sandbox.destroy()

    async def _copy_workspace_via_tar(
        self,
        scope_id: str,
        from_sandbox: Sandbox,
        to_sandbox: Sandbox,
    ) -> str | None:
        """Fallback: Copy workspace via tar archive.

        Used when backends don't support workspace sync.

        Args:
            scope_id: Scope being migrated.
            from_sandbox: Source sandbox.
            to_sandbox: Destination sandbox.

        Returns:
            SHA256 of tar contents.

        Raises:
            MigrationBlockedError: If extraction fails.
        """
        tar_result = await from_sandbox.exec(
            ["tar", "-czf", "-", "-C", "/workspace", "."],
            timeout=300.0,
        )

        if not tar_result.success:
            return None

        tar_data = tar_result.stdout
        migration_sha = hashlib.sha256(tar_data).hexdigest()

        await to_sandbox.write_file("/tmp/workspace.tar.gz", tar_data)

        extract_result = await to_sandbox.exec(
            ["tar", "-xzf", "/tmp/workspace.tar.gz", "-C", "/workspace"],
            timeout=300.0,
        )

        if not extract_result.success:
            raise MigrationBlockedError(
                f"failed to extract workspace: {extract_result.stderr_text}",
                scope_id=scope_id,
                to_backend=to_sandbox.info.provider,
            )

        return migration_sha

    @asynccontextmanager
    async def _maybe_lock(self, key: str) -> AsyncIterator[None]:
        """Acquire advisory lock if available."""
        if self._lock is None:
            yield
            return

        async with self._lock.acquire(key):
            yield
