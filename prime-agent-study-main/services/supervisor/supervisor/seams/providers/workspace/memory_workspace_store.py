"""In-memory WorkspaceStore + tree IO for dual-SoT contract tests."""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime

from supervisor.seams.providers.workspace.sync import base_of, three_way_sync
from supervisor.seams.services.object_storage import ObjectStorage
from supervisor.seams.services.workspace_store import (
    CasConflict,
    ChecksumMismatch,
    HydrateIncomplete,
    WorkspaceFileRecord,
    WorkspaceFileVersion,
    WorkspaceSyncReport,
    WorkspaceTreeIO,
    new_object_key,
    normalize_workspace_path,
)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@dataclass
class MemoryTreeIO:
    files: dict[str, bytes] = field(default_factory=dict)

    async def write_bytes(self, path: str, data: bytes) -> None:
        self.files[normalize_workspace_path(path)] = data

    async def read_bytes(self, path: str) -> bytes:
        key = normalize_workspace_path(path)
        try:
            return self.files[key]
        except KeyError as exc:
            raise FileNotFoundError(path) from exc

    async def list_paths(self) -> list[str]:
        return sorted(self.files)

    async def delete_path(self, path: str) -> None:
        self.files.pop(normalize_workspace_path(path), None)

    async def clear(self) -> None:
        self.files.clear()


@dataclass
class _Meta:
    file_id: str
    scope_id: str
    path: str
    content_sha256: str
    object_key: str
    size_bytes: int
    version: int
    mime_type: str | None = None
    deleted: bool = False

    def to_record(self) -> WorkspaceFileRecord:
        return WorkspaceFileRecord(
            file_id=self.file_id,
            scope_id=self.scope_id,
            path=self.path,
            content_sha256=self.content_sha256,
            object_key=self.object_key,
            size_bytes=self.size_bytes,
            version=self.version,
            mime_type=self.mime_type,
            deleted=self.deleted,
        )


@dataclass
class MemoryWorkspaceStore:
    objects: ObjectStorage
    _rows: dict[tuple[str, str], _Meta] = field(default_factory=dict)
    _versions: list[WorkspaceFileVersion] = field(default_factory=list)
    # WP-M6 chaos: invoked immediately before CAS commit during sync (R10).
    before_sync_cas: object | None = None

    async def list_files(self, scope_id: str) -> list[WorkspaceFileRecord]:
        return [
            row.to_record()
            for (sid, _), row in sorted(self._rows.items())
            if sid == scope_id and not row.deleted
        ]

    async def stat(self, scope_id: str, path: str) -> WorkspaceFileRecord | None:
        path = normalize_workspace_path(path)
        row = self._rows.get((scope_id, path))
        if row is None or row.deleted:
            return None
        return row.to_record()

    async def commit_blob(
        self,
        scope_id: str,
        path: str,
        data: bytes,
        *,
        mime_type: str | None = None,
        expected_version: int | None = None,
        author: str | None = None,
    ) -> WorkspaceFileRecord:
        path = normalize_workspace_path(path)
        existing = self._rows.get((scope_id, path))
        if expected_version is not None:
            actual = 0 if existing is None or existing.deleted else existing.version
            if actual != expected_version:
                raise CasConflict(path, expected_version, actual)
        digest = _sha256(data)
        key = await self.find_object(scope_id, digest, len(data))
        if key is None:
            key = new_object_key(scope_id)
            await self.objects.put(key, data, content_sha256=digest)
        return await self.commit_object(
            scope_id,
            path,
            content_sha256=digest,
            size_bytes=len(data),
            object_key=key,
            mime_type=mime_type,
            expected_version=expected_version,
            author=author,
        )

    async def find_object(self, scope_id: str, content_sha256: str, size_bytes: int) -> str | None:
        for v in self._versions:
            if (v.scope_id, v.content_sha256, v.size_bytes) == (scope_id, content_sha256, size_bytes):
                return v.object_key
        return None

    async def list_versions(self, scope_id: str, path: str) -> list[WorkspaceFileVersion]:
        path = normalize_workspace_path(path)
        found = [v for v in self._versions if (v.scope_id, v.path) == (scope_id, path)]
        return sorted(found, key=lambda v: v.version, reverse=True)

    async def get_version(self, scope_id: str, path: str, version: int) -> WorkspaceFileVersion | None:
        path = normalize_workspace_path(path)
        return next(
            (v for v in self._versions if (v.scope_id, v.path, v.version) == (scope_id, path, version)),
            None,
        )

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
        path = normalize_workspace_path(path)
        key = (scope_id, path)
        existing = self._rows.get(key)
        if expected_version is not None:
            actual = 0 if existing is None or existing.deleted else existing.version
            if actual != expected_version:
                raise CasConflict(path, expected_version, actual)
        if existing is None:
            version = 1
            file_id = uuid.uuid4().hex
        elif existing.deleted:  # versions keep counting through a delete
            version = existing.version + 1
            file_id = uuid.uuid4().hex
        else:
            version = existing.version + 1
            file_id = existing.file_id

        row = _Meta(
            file_id=file_id,
            scope_id=scope_id,
            path=path,
            content_sha256=content_sha256,
            object_key=object_key,
            size_bytes=size_bytes,
            version=version,
            mime_type=mime_type,
            deleted=False,
        )
        self._rows[key] = row
        self._versions.append(
            WorkspaceFileVersion(
                scope_id=scope_id,
                path=path,
                version=version,
                content_sha256=content_sha256,
                object_key=object_key,
                size_bytes=size_bytes,
                mime_type=mime_type,
                created_at=datetime.now(UTC),
                author=author,
                restored_from=restored_from,
            )
        )
        return row.to_record()

    async def soft_delete(
        self, scope_id: str, path: str, *, expected_version: int | None = None
    ) -> None:
        path = normalize_workspace_path(path)
        row = self._rows.get((scope_id, path))
        live = row is not None and not row.deleted
        actual = row.version if live and row is not None else 0
        if expected_version is not None and actual != expected_version:
            raise CasConflict(path, expected_version, actual)
        if row is None or row.deleted:
            return
        row.deleted = True

    async def hydrate(
        self, scope_id: str, tree: WorkspaceTreeIO
    ) -> WorkspaceSyncReport:
        written: list[str] = []
        bytes_n = 0
        records = await self.list_files(scope_id)
        try:
            await tree.clear()
            for rec in records:
                data = await self.objects.get(rec.object_key)
                actual = _sha256(data)
                if actual != rec.content_sha256:
                    raise ChecksumMismatch(rec.object_key, rec.content_sha256, actual)
                await tree.write_bytes(rec.path, data)
                written.append(rec.path)
                bytes_n += len(data)
        except ChecksumMismatch:
            raise
        except Exception as exc:
            raise HydrateIncomplete(scope_id, str(exc)) from exc
        return WorkspaceSyncReport(
            scope_id=scope_id,
            written=tuple(written),
            removed=(),
            conflicts=(),
            bytes_transferred=bytes_n,
            base=base_of(records),
        )

    async def sync(
        self,
        scope_id: str,
        tree: WorkspaceTreeIO,
        *,
        base: Mapping[str, tuple[int, str]] | None = None,
    ) -> WorkspaceSyncReport:
        return await three_way_sync(
            self, scope_id, tree, base, before_cas=self.before_sync_cas  # type: ignore[arg-type]
        )


def as_tree_io(tree: MemoryTreeIO) -> WorkspaceTreeIO:
    return tree
