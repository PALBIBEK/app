"""Factory for WorkspaceStore implementations (memory + postgres)."""

from __future__ import annotations

import os

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from supervisor.seams.providers.storage.memory_object_storage import MemoryObjectStorage
from supervisor.seams.providers.storage.object_storage_factory import create_object_storage
from supervisor.seams.providers.workspace.memory_workspace_store import MemoryWorkspaceStore
from supervisor.seams.services.object_storage import ObjectStorage
from supervisor.seams.services.workspace_hydrator import (
    StoreWorkspaceHydrator,
    WorkspaceHydrator,
)
from supervisor.seams.services.workspace_store import WorkspaceStore

DEFAULT_MAX_SCOPE_BYTES = 10 * 1024 * 1024 * 1024


def max_scope_bytes() -> int | None:
    """Most a workspace may store (``WORKSPACE_MAX_SCOPE_BYTES``, default 10 GiB; 0: no limit)."""
    raw = os.environ.get("WORKSPACE_MAX_SCOPE_BYTES", "").strip()
    if not raw:
        return DEFAULT_MAX_SCOPE_BYTES
    limit = int(raw)
    return None if limit <= 0 else limit


def _env_is_staging() -> bool:
    return (os.environ.get("ENV") or "").strip().lower() in ("staging", "production", "prod")


def create_workspace_store(
    name: str | None = None,
    *,
    objects: ObjectStorage | None = None,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> WorkspaceStore:
    """Select store from ``WORKSPACE_STORE`` or explicit name.

    Names: ``memory`` (default), ``postgres`` (requires ``session_factory``).
    """
    chosen = (name or os.environ.get("WORKSPACE_STORE") or "memory").strip().lower()
    objs = objects or MemoryObjectStorage()
    if chosen == "memory":
        if _env_is_staging():
            raise RuntimeError(
                "WORKSPACE_STORE=memory is forbidden when ENV=staging; use postgres"
            )
        return MemoryWorkspaceStore(objects=objs)
    if chosen in ("postgres", "pg", "sql"):
        if session_factory is None:
            raise ValueError(
                "WORKSPACE_STORE=postgres requires session_factory"
            )
        from supervisor.seams.providers.workspace.postgres_workspace_store import (
            PostgresWorkspaceStore,
        )

        return PostgresWorkspaceStore(session_factory, objs, max_scope_bytes=max_scope_bytes())
    raise ValueError(f"unknown WORKSPACE_STORE={chosen!r}")


def create_workspace_hydrator(
    name: str | None = None,
    *,
    objects: ObjectStorage | None = None,
    store: WorkspaceStore | None = None,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> WorkspaceHydrator:
    """Composition-root helper: store + per-scope mutex."""
    resolved = store or create_workspace_store(
        name, objects=objects, session_factory=session_factory
    )
    return StoreWorkspaceHydrator(resolved)


def create_object_storage_for_workspace() -> ObjectStorage:
    """Prefer OBJECT_STORAGE env; fall back to memory for unit/bootstrap."""
    chosen = (os.environ.get("OBJECT_STORAGE") or "memory").strip().lower()
    if chosen in ("memory", "mem", "fake", ""):
        if _env_is_staging():
            raise RuntimeError(
                "OBJECT_STORAGE=memory is forbidden when ENV=staging; use seaweed/s3"
            )
        return MemoryObjectStorage()
    return create_object_storage(chosen)
