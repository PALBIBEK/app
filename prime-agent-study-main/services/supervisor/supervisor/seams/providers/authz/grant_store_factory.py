"""Factory for GrantStore / AuthzService (memory + postgres)."""

from __future__ import annotations

import os

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from supervisor.seams.providers.authz.memory_grant_store import MemoryGrantStore
from supervisor.seams.services.authz import AuthzService, GrantStore
from supervisor.seams.services.authz_service import StoreAuthzService


def create_grant_store(
    name: str | None = None,
    *,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> GrantStore:
    """Select store from ``GRANT_STORE`` or explicit name.

    Names: ``memory`` (default), ``postgres`` (requires ``session_factory``).
    """
    chosen = (name or os.environ.get("GRANT_STORE") or "memory").strip().lower()
    if chosen == "memory":
        return MemoryGrantStore()
    if chosen in ("postgres", "pg", "sql"):
        if session_factory is None:
            raise ValueError("GRANT_STORE=postgres requires session_factory")
        from supervisor.seams.providers.authz.postgres_grant_store import PostgresGrantStore

        return PostgresGrantStore(session_factory)
    raise ValueError(f"unknown GRANT_STORE={chosen!r}")


def create_authz_service(
    name: str | None = None,
    *,
    store: GrantStore | None = None,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> AuthzService:
    """Composition-root helper: GrantStore + AuthzService."""
    resolved = store or create_grant_store(name, session_factory=session_factory)
    return StoreAuthzService(resolved)
