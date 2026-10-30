"""Session workspace hydrate/dirty/sync helpers (WP-M5/M6).

Authz stays outside WorkspaceHydrator — callers use authorized_* helpers.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from supervisor.sandbox.base import Sandbox, WorkspaceSyncUnsupported
from supervisor.seams.services.authorized_workspace import authorized_hydrate, authorized_sync
from supervisor.seams.services.authz import AuthzService, Grant, Principal
from supervisor.seams.services.workspace_hydrator import WorkspaceHydrator
from supervisor.seams.services.workspace_store import (
    HydrateIncomplete,
    WorkspacePathEscape,
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
    base: Mapping[str, tuple[int, str]] | None = None
    """What the sandbox's tree started from (the last hydrate or sync); None: unknown."""


class WorkspaceConflictError(RuntimeError):
    """CAS / sync conflicts — fail closed (no last-write-wins)."""

    def __init__(self, scope_id: str, conflicts: tuple[str, ...]) -> None:
        self.scope_id = scope_id
        self.conflicts = conflicts
        super().__init__(
            f"workspace sync conflicts scope={scope_id!r} paths={list(conflicts)}"
        )


class WorkspaceFullError(RuntimeError):
    """Files the session changed were not saved: they do not fit the workspace's storage limit.
    The session stays dirty, so the next sync tries them again."""

    def __init__(self, scope_id: str, paths: tuple[str, ...]) -> None:
        self.scope_id = scope_id
        self.paths = paths
        super().__init__(f"workspace full scope={scope_id!r} paths={list(paths)}")


class WorkspaceNotReadyError(RuntimeError):
    """Tools refused until hydrate succeeds (R8)."""

    def __init__(self, scope_id: str | None) -> None:
        self.scope_id = scope_id
        super().__init__(f"workspace not ready scope={scope_id!r}")


RESERVED_DIRS = (".attachments", ".outputs")
"""Sandbox folders that are not the workspace: a run's attached files (read-only copies of
pinned versions) and the files it makes for the user (collected as artifacts). They are never
copied into the workspace, and refreshing the workspace leaves them alone."""


def is_reserved(path: str) -> bool:
    first = path.strip().lstrip("/").split("/", 1)[0]
    return first in RESERVED_DIRS


class ScopeMirrorTree:
    """A sandbox tree as the workspace sees it: the reserved folders hidden and untouchable."""

    def __init__(self, inner: WorkspaceTreeIO) -> None:
        self._inner = inner

    async def write_bytes(self, path: str, data: bytes) -> None:
        if is_reserved(path):
            raise WorkspacePathEscape(path)
        await self._inner.write_bytes(path, data)

    async def read_bytes(self, path: str) -> bytes:
        if is_reserved(path):
            raise FileNotFoundError(path)
        return await self._inner.read_bytes(path)

    async def list_paths(self) -> list[str]:
        return [p for p in await self._inner.list_paths() if not is_reserved(p)]

    async def delete_path(self, path: str) -> None:
        if is_reserved(path):
            raise WorkspacePathEscape(path)
        await self._inner.delete_path(path)

    async def clear(self) -> None:
        for path in await self.list_paths():
            await self._inner.delete_path(path)


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

    @property
    def hydrator(self) -> WorkspaceHydrator | None:
        """The deployment's workspace hydrator (every sandbox provider must use it)."""
        return self._hydrator

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
        tree = ScopeMirrorTree(tree_for_sandbox(sandbox))
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
        state.base = report.base
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
        author: str | None = None,
        source: str | None = None,
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
        tree = ScopeMirrorTree(tree_for_sandbox(sandbox))
        report = await authorized_sync(
            self._authz,
            self._hydrator,
            state.principal,
            state.scope_id,
            tree,
            base=state.base,
            author=author,
            source=source,
        )
        state.last_sync = report
        state.base = report.base
        if report.conflicts:
            state.conflicts = report.conflicts
            raise WorkspaceConflictError(state.scope_id, report.conflicts)
        if report.refused:
            raise WorkspaceFullError(state.scope_id, report.refused)
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
