"""WorkspaceHydrator — control-plane collaborator for durable ↔ sandbox cache.

Sandbox providers inject this; Sandbox façades only adapt FS → WorkspaceTreeIO.
Hydrate/sync policy lives on WorkspaceStore (Modal reload/commit semantics).
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Protocol, runtime_checkable

from supervisor.seams.services.workspace_store import (
    WorkspaceStore,
    WorkspaceSyncReport,
    WorkspaceTreeIO,
)

__all__ = [
    "StoreWorkspaceHydrator",
    "WorkspaceHydrator",
]


@runtime_checkable
class WorkspaceHydrator(Protocol):
    """Lifecycle ops only — not spawn/exec. ``scope_id`` always from control plane."""

    async def hydrate(
        self, scope_id: str, tree: WorkspaceTreeIO
    ) -> WorkspaceSyncReport: ...

    async def sync(
        self,
        scope_id: str,
        tree: WorkspaceTreeIO,
        *,
        base: Mapping[str, tuple[int, str]] | None = None,
        author: str | None = None,
        source: str | None = None,
    ) -> WorkspaceSyncReport: ...


class StoreWorkspaceHydrator:
    """Thin DI wrapper: per-scope mutex + delegate to WorkspaceStore."""

    def __init__(self, store: WorkspaceStore) -> None:
        self._store = store
        self._locks: dict[str, asyncio.Lock] = {}

    def _lock_for(self, scope_id: str) -> asyncio.Lock:
        lock = self._locks.get(scope_id)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[scope_id] = lock
        return lock

    async def hydrate(
        self, scope_id: str, tree: WorkspaceTreeIO
    ) -> WorkspaceSyncReport:
        async with self._lock_for(scope_id):
            return await self._store.hydrate(scope_id, tree)

    async def sync(
        self,
        scope_id: str,
        tree: WorkspaceTreeIO,
        *,
        base: Mapping[str, tuple[int, str]] | None = None,
        author: str | None = None,
        source: str | None = None,
    ) -> WorkspaceSyncReport:
        async with self._lock_for(scope_id):
            return await self._store.sync(scope_id, tree, base=base, author=author, source=source)


