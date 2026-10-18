"""WorkspaceStore seam — Postgres metadata + ObjectStorage orchestration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from supervisor.seams.core.errors import SeamError

__all__ = [
    "CasConflict",
    "WorkspaceFileRecord",
    "ChecksumMismatch",
    "HydrateIncomplete",
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
class WorkspaceSyncReport:
    scope_id: str
    written: tuple[str, ...]
    removed: tuple[str, ...]
    conflicts: tuple[str, ...]
    bytes_transferred: int


@runtime_checkable
class WorkspaceTreeIO(Protocol):
    async def write_bytes(self, path: str, data: bytes) -> None: ...

    async def read_bytes(self, path: str) -> bytes: ...

    async def list_paths(self) -> list[str]: ...

    async def delete_path(self, path: str) -> None: ...


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
    ) -> WorkspaceFileRecord: ...

    async def soft_delete(self, scope_id: str, path: str) -> None: ...

    async def hydrate(
        self, scope_id: str, tree: WorkspaceTreeIO
    ) -> WorkspaceSyncReport: ...

    async def sync(
        self, scope_id: str, tree: WorkspaceTreeIO
    ) -> WorkspaceSyncReport: ...
