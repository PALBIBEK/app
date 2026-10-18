"""Session workspace hydrate/dirty/sync helpers (WP-M5/M6).

Authz stays outside WorkspaceHydrator — callers use authorized_* helpers.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from supervisor.sandbox.base import Sandbox, WorkspaceSyncUnsupported
from supervisor.seams.services.authz import AuthzService, Grant, Principal
from supervisor.seams.services.authorized_workspace import authorized_hydrate, authorized_sync
from supervisor.seams.services.workspace_hydrator import WorkspaceHydrator
from supervisor.seams.services.workspace_store import (
    HydrateIncomplete,
    WorkspaceSyncReport,
    WorkspaceTreeIO,
)


@dataclass
class SessionWorkspaceState:
    scope_id: str | None = None
    principal: Principal | None = None
    dirty: bool = False
    ready: bool = False
    virtual_key_token: str | None = None
    virtual_key_id: str | None = None
    last_sync: WorkspaceSyncReport | None = None
    conflicts: tuple[str, ...] = field(default_factory=tuple)


class WorkspaceConflictError(RuntimeError):
    """CAS / sync conflicts — fail closed (no last-write-wins)."""

    def __init__(self, scope_id: str, conflicts: tuple[str, ...]) -> None:
        self.scope_id = scope_id
        self.conflicts = conflicts
        super().__init__(
            f"workspace sync conflicts scope={scope_id!r} paths={list(conflicts)}"
        )


class WorkspaceNotReadyError(RuntimeError):
    """Tools refused until hydrate succeeds (R8)."""

    def __init__(self, scope_id: str | None) -> None:
        self.scope_id = scope_id
        super().__init__(f"workspace not ready scope={scope_id!r}")


def tree_for_sandbox(sandbox: Sandbox) -> WorkspaceTreeIO:
    as_tree = getattr(sandbox, "as_tree_io", None)
    if callable(as_tree):
        return as_tree()
    raise WorkspaceSyncUnsupported(
        f"{type(sandbox).__name__} cannot expose WorkspaceTreeIO"
    )


class SessionWorkspaceService:
    """Control-plane collaborator for hydrate / dirty / sync on a live session."""

    def __init__(
        self,
        *,
        authz: AuthzService | None = None,
        hydrator: WorkspaceHydrator | None = None,
    ) -> None:
        self._authz = authz
        self._hydrator = hydrator

    def assert_ready(self, state: SessionWorkspaceState) -> None:
        """Refuse tools while hydrate incomplete (R8). No-op if no scope bound."""
        if state.scope_id is None:
            return
        if not state.ready:
            raise WorkspaceNotReadyError(state.scope_id)

    async def ensure_hydrated(
        self,
        state: SessionWorkspaceState,
        sandbox: Sandbox | None,
        *,
        force: bool = False,
    ) -> WorkspaceSyncReport | None:
        if state.scope_id is None or sandbox is None:
            return None
        if state.ready and not force:
            return None
        if self._authz is None or self._hydrator is None or state.principal is None:
            raise RuntimeError(
                "scope-bound session requires authz + hydrator + principal"
            )
        tree = tree_for_sandbox(sandbox)
        state.ready = False
        try:
            report = await authorized_hydrate(
                self._authz,
                self._hydrator,
                state.principal,
                state.scope_id,
                tree,
            )
        except HydrateIncomplete:
            state.ready = False
            raise
        except Exception:
            state.ready = False
            raise
        state.ready = True
        return report

    def mark_dirty(self, state: SessionWorkspaceState) -> None:
        if state.scope_id is not None:
            state.dirty = True

    async def sync_if_dirty(
        self,
        state: SessionWorkspaceState,
        sandbox: Sandbox | None,
        *,
        force: bool = False,
    ) -> WorkspaceSyncReport | None:
        if state.scope_id is None or sandbox is None:
            return None
        if not state.dirty and not force:
            return None
        if self._authz is None or self._hydrator is None or state.principal is None:
            raise RuntimeError(
                "scope-bound session requires authz + hydrator + principal"
            )
        await self._authz.require(
            state.principal, state.scope_id, Grant.WORKSPACE_WRITE
        )
        tree = tree_for_sandbox(sandbox)
        report = await authorized_sync(
            self._authz,
            self._hydrator,
            state.principal,
            state.scope_id,
            tree,
        )
        state.last_sync = report
        if report.conflicts:
            state.conflicts = report.conflicts
            raise WorkspaceConflictError(state.scope_id, report.conflicts)
        state.dirty = False
        return report

    async def require_sandbox_exec(
        self, state: SessionWorkspaceState
    ) -> None:
        if state.scope_id is None:
            return
        if state.principal is None or self._authz is None:
            raise RuntimeError(
                "scope-bound session requires authz + principal before sandbox create"
            )
        await self._authz.require(
            state.principal, state.scope_id, Grant.SANDBOX_EXEC
        )

    async def recover_cas_conflict(
        self,
        state: SessionWorkspaceState,
        sandbox: Sandbox,
    ) -> WorkspaceSyncReport | None:
        """Replace sandbox cache with durable SoT after a CAS conflict (durable wins)."""
        report = await self.ensure_hydrated(state, sandbox, force=True)
        state.dirty = False
        state.conflicts = ()
        return report
