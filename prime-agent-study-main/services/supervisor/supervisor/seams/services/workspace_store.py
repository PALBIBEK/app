"""WorkspaceStore seam — Postgres metadata + ObjectStorage orchestration."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol, runtime_checkable
from uuid import uuid4

from supervisor.seams.core.errors import SeamError

__all__ = [
    "CasConflict",
    "ChecksumMismatch",
    "HydrateIncomplete",
    "WorkspaceBase",
    "WorkspaceFileRecord",
    "WorkspacePathEscape",
    "WorkspaceStore",
    "WorkspaceStoreError",
    "WorkspaceSyncReport",
    "WorkspaceTreeIO",
]


class WorkspaceStoreError(SeamError):
    def __init__(self, message: str, code: str) -> None:
        super().__init__(message, code)


class CasConflict(WorkspaceStoreError):
    def __init__(self, path: str, expected: int, actual: int) -> None:
        super().__init__(
            f"CAS conflict path={path!r} expected={expected} actual={actual}",
            "WORKSPACE_CAS_CONFLICT",
        )
        self.path = path
        self.expected = expected
        self.actual = actual


class WorkspacePathEscape(WorkspaceStoreError):
    def __init__(self, path: str) -> None:
        super().__init__(f"path escapes workspace: {path!r}", "WORKSPACE_PATH_ESCAPE")
        self.path = path



class ChecksumMismatch(WorkspaceStoreError):
    """Object bytes do not match durable ``content_sha256`` (R11)."""

    def __init__(self, object_key: str, expected: str, actual: str) -> None:
        super().__init__(
            f"checksum mismatch key={object_key!r} expected={expected} actual={actual}",
            "WORKSPACE_CHECKSUM_MISMATCH",
        )
        self.object_key = object_key
        self.expected = expected
        self.actual = actual


class HydrateIncomplete(WorkspaceStoreError):
    """Cache left in an unsafe mid-hydrate state (R8)."""

    def __init__(self, scope_id: str, message: str = "hydrate incomplete") -> None:
        super().__init__(f"{message} scope={scope_id!r}", "WORKSPACE_HYDRATE_INCOMPLETE")
        self.scope_id = scope_id


@dataclass(frozen=True)
class WorkspaceFileRecord:
    file_id: str
    scope_id: str
    path: str
    content_sha256: str
    object_key: str
    size_bytes: int
    version: int
    mime_type: str | None = None
    deleted: bool = False


@dataclass(frozen=True)
class WorkspaceFileVersion:
    """One saved state of a path: its bytes (by object key) as they were at ``version``."""

    scope_id: str
    path: str
    version: int
    content_sha256: str
    object_key: str
    size_bytes: int
    mime_type: str | None
    created_at: datetime
    author: str | None = None
    restored_from: int | None = None


def normalize_workspace_path(path: str) -> str:
    """A workspace path in canonical form (no leading slash, no ``.``/empty parts); refuses
    ``..`` and empty paths (WorkspacePathEscape)."""
    raw = path.strip().replace("\\", "/")
    raw = raw.removeprefix("/")
    parts: list[str] = []
    for part in raw.split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            raise WorkspacePathEscape(path)
        parts.append(part)
    if not parts:
        raise WorkspacePathEscape(path)
    return "/".join(parts)


def new_object_key(scope_id: str) -> str:
    """Where a new version's bytes go: private to the workspace and opaque (never the path or
    the content hash, so storage reveals nothing about names or about other workspaces)."""
    return f"workspaces/{scope_id}/objects/{uuid4().hex}"


type WorkspaceBase = dict[str, tuple[int, str]]
"""What a session's tree started from: path -> (version, sha256) in the scope."""


@dataclass(frozen=True)
class WorkspaceSyncReport:
    scope_id: str
    written: tuple[str, ...]
    removed: tuple[str, ...]
    conflicts: tuple[str, ...]
    bytes_transferred: int
    base: Mapping[str, tuple[int, str]] = field(default_factory=dict)
    """The tree's base after this hydrate or sync: pass it to the next sync."""


@runtime_checkable
class WorkspaceTreeIO(Protocol):
    async def write_bytes(self, path: str, data: bytes) -> None: ...

    async def read_bytes(self, path: str) -> bytes: ...

    async def list_paths(self) -> list[str]: ...

    async def delete_path(self, path: str) -> None: ...

    async def clear(self) -> None:
        """Remove every file (hydrate starts from an empty tree)."""
        ...


@runtime_checkable
class WorkspaceStore(Protocol):
    async def list_files(self, scope_id: str) -> list[WorkspaceFileRecord]: ...

    async def stat(self, scope_id: str, path: str) -> WorkspaceFileRecord | None: ...

    async def commit_blob(
        self,
        scope_id: str,
        path: str,
        data: bytes,
        *,
        mime_type: str | None = None,
        expected_version: int | None = None,
        author: str | None = None,
    ) -> WorkspaceFileRecord: ...

    async def commit_object(
        self,
        scope_id: str,
        path: str,
        *,
        content_sha256: str,
        size_bytes: int,
        object_key: str,
        mime_type: str | None = None,
        expected_version: int | None = None,
        author: str | None = None,
        restored_from: int | None = None,
    ) -> WorkspaceFileRecord:
        """Make ``path`` a new version holding the bytes already stored at ``object_key``."""
        ...

    async def find_object(self, scope_id: str, content_sha256: str, size_bytes: int) -> str | None:
        """The key of bytes this workspace already stores with this SHA-256 (any path or version)."""
        ...

    async def list_versions(self, scope_id: str, path: str) -> list[WorkspaceFileVersion]:
        """Every saved version of ``path``, newest first (kept through deletes)."""
        ...

    async def get_version(self, scope_id: str, path: str, version: int) -> WorkspaceFileVersion | None: ...

    async def soft_delete(
        self, scope_id: str, path: str, *, expected_version: int | None = None
    ) -> None: ...

    async def hydrate(
        self, scope_id: str, tree: WorkspaceTreeIO
    ) -> WorkspaceSyncReport: ...

    async def sync(
        self,
        scope_id: str,
        tree: WorkspaceTreeIO,
        *,
        base: Mapping[str, tuple[int, str]] | None = None,
    ) -> WorkspaceSyncReport:
        """Commit what the tree changed since ``base`` (see providers.workspace.sync)."""
        ...
