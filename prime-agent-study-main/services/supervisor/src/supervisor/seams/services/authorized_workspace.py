"""Control-plane helpers that gate hydrate/sync behind grants (WP-M4).

Callers (SessionManager / HTTP) use these — never put GrantStore inside
WorkspaceHydrator or LocalProcessSandbox.
"""

from __future__ import annotations

from collections.abc import Mapping

from supervisor.seams.services.authz import AuthzService, Grant, Principal
from supervisor.seams.services.workspace_hydrator import WorkspaceHydrator
from supervisor.seams.services.workspace_store import WorkspaceSyncReport, WorkspaceTreeIO

__all__ = [
    "authorized_exec",
    "authorized_hydrate",
    "authorized_sync",
]


async def authorized_hydrate(
    authz: AuthzService,
    hydrator: WorkspaceHydrator,
    principal: Principal,
    scope_id: str,
    tree: WorkspaceTreeIO,
) -> WorkspaceSyncReport:
    await authz.require(principal, scope_id, Grant.WORKSPACE_READ)
    return await hydrator.hydrate(scope_id, tree)


async def authorized_sync(
    authz: AuthzService,
    hydrator: WorkspaceHydrator,
    principal: Principal,
    scope_id: str,
    tree: WorkspaceTreeIO,
    *,
    base: Mapping[str, tuple[int, str]] | None = None,
) -> WorkspaceSyncReport:
    await authz.require(principal, scope_id, Grant.WORKSPACE_WRITE)
    return await hydrator.sync(scope_id, tree, base=base)


async def authorized_exec(
    authz: AuthzService,
    principal: Principal,
    scope_id: str,
) -> Principal:
    """Gate sandbox create / exec behind ``sandbox:exec``."""
    return await authz.require(principal, scope_id, Grant.SANDBOX_EXEC)
