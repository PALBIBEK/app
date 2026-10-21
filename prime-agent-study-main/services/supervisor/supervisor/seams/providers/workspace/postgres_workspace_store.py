"""Postgres-backed WorkspaceStore (Stage 1 / D9 durable metadata SoT)."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from supervisor.db.models import ScopeRow, WorkspaceFileRow, WorkspaceFileVersionRow
from supervisor.seams.providers.workspace.memory_workspace_store import normalize_workspace_path
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
)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _version_of(row: WorkspaceFileVersionRow) -> WorkspaceFileVersion:
    return WorkspaceFileVersion(
        scope_id=row.scope_id,
        path=row.path,
        version=int(row.version),
        content_sha256=row.content_sha256,
        object_key=row.object_key,
        size_bytes=int(row.size_bytes),
        mime_type=row.mime_type,
        created_at=row.created_at,
        author=row.author,
        restored_from=row.restored_from,
    )


def _row_to_record(row: WorkspaceFileRow) -> WorkspaceFileRecord:
    return WorkspaceFileRecord(
        file_id=row.id,
        scope_id=row.scope_id,
        path=row.path,
        content_sha256=row.content_sha256,
        object_key=row.object_key,
        size_bytes=int(row.size_bytes),
        version=int(row.version),
        mime_type=row.mime_type,
        deleted=row.deleted_at is not None,
    )


class PostgresWorkspaceStore:
    """WorkspaceStore over ``workspace_files`` + injected ObjectStorage."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        objects: ObjectStorage,
    ) -> None:
        self._db = session_factory
        self.objects = objects
        # Chaos hook parity with MemoryWorkspaceStore (optional).
        self.before_sync_cas: object | None = None

    async def _ensure_scope(self, db: AsyncSession, scope_id: str) -> None:
        scope = await db.get(ScopeRow, scope_id)
        if scope is None:
            db.add(
                ScopeRow(
                    id=scope_id,
                    type="personal",
                    owner_principal_id="system",
                    name=scope_id,
                )
            )
            await db.flush()

    async def list_files(self, scope_id: str) -> list[WorkspaceFileRecord]:
        async with self._db() as db:
            rows = (
                await db.scalars(
                    select(WorkspaceFileRow)
                    .where(
                        WorkspaceFileRow.scope_id == scope_id,
                        WorkspaceFileRow.deleted_at.is_(None),
                    )
                    .order_by(WorkspaceFileRow.path)
                )
            ).all()
            return [_row_to_record(r) for r in rows]

    async def stat(self, scope_id: str, path: str) -> WorkspaceFileRecord | None:
        path = normalize_workspace_path(path)
        async with self._db() as db:
            row = await db.scalar(
                select(WorkspaceFileRow).where(
                    WorkspaceFileRow.scope_id == scope_id,
                    WorkspaceFileRow.path == path,
                    WorkspaceFileRow.deleted_at.is_(None),
                )
            )
            return None if row is None else _row_to_record(row)

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
        digest = _sha256(data)
        key = await self.find_object(scope_id, digest, len(data))
        if key is None:  # new bytes for this workspace: a key of their own
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
        async with self._db() as db:
            return await db.scalar(
                select(WorkspaceFileVersionRow.object_key)
                .where(
                    WorkspaceFileVersionRow.scope_id == scope_id,
                    WorkspaceFileVersionRow.content_sha256 == content_sha256,
                    WorkspaceFileVersionRow.size_bytes == size_bytes,
                )
                .limit(1)
            )

    async def list_versions(self, scope_id: str, path: str) -> list[WorkspaceFileVersion]:
        path = normalize_workspace_path(path)
        async with self._db() as db:
            rows = (
                await db.scalars(
                    select(WorkspaceFileVersionRow)
                    .where(WorkspaceFileVersionRow.scope_id == scope_id, WorkspaceFileVersionRow.path == path)
                    .order_by(WorkspaceFileVersionRow.version.desc())
                )
            ).all()
            return [_version_of(r) for r in rows]

    async def get_version(self, scope_id: str, path: str, version: int) -> WorkspaceFileVersion | None:
        path = normalize_workspace_path(path)
        async with self._db() as db:
            row = await db.scalar(
                select(WorkspaceFileVersionRow).where(
                    WorkspaceFileVersionRow.scope_id == scope_id,
                    WorkspaceFileVersionRow.path == path,
                    WorkspaceFileVersionRow.version == version,
                )
            )
            return None if row is None else _version_of(row)

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
        async with self._db() as db:
            await self._ensure_scope(db, scope_id)
            live = await db.scalar(
                select(WorkspaceFileRow).where(
                    WorkspaceFileRow.scope_id == scope_id,
                    WorkspaceFileRow.path == path,
                    WorkspaceFileRow.deleted_at.is_(None),
                )
            )
            actual = 0 if live is None else int(live.version)
            if expected_version is not None and actual != expected_version:
                raise CasConflict(path, expected_version, actual)

            row = live
            if row is None:
                # Revive a soft-deleted row in place if present (full-unique schema), else insert.
                row = await db.scalar(
                    select(WorkspaceFileRow).where(
                        WorkspaceFileRow.scope_id == scope_id,
                        WorkspaceFileRow.path == path,
                        WorkspaceFileRow.deleted_at.is_not(None),
                    )
                )
            if row is None:
                row = WorkspaceFileRow(id=uuid4().hex, scope_id=scope_id, path=path, version=0)
                db.add(row)
            # Versions keep counting through a delete, so a version a session saw before can
            # never name a different file, and history stays in order.
            row.version = int(row.version or 0) + 1
            row.content_sha256 = content_sha256
            row.object_key = object_key
            row.size_bytes = size_bytes
            row.mime_type = mime_type
            row.deleted_at = None
            row.updated_at = _utcnow()
            db.add(
                WorkspaceFileVersionRow(
                    id=uuid4().hex,
                    scope_id=scope_id,
                    path=path,
                    version=row.version,
                    content_sha256=content_sha256,
                    object_key=object_key,
                    size_bytes=size_bytes,
                    mime_type=mime_type,
                    author=author,
                    restored_from=restored_from,
                    created_at=_utcnow(),
                )
            )
            await db.commit()
            await db.refresh(row)
            return _row_to_record(row)

    async def soft_delete(
        self, scope_id: str, path: str, *, expected_version: int | None = None
    ) -> None:
        path = normalize_workspace_path(path)
        async with self._db() as db:
            row = await db.scalar(
                select(WorkspaceFileRow).where(
                    WorkspaceFileRow.scope_id == scope_id,
                    WorkspaceFileRow.path == path,
                    WorkspaceFileRow.deleted_at.is_(None),
                )
            )
            actual = 0 if row is None else int(row.version)
            if expected_version is not None and actual != expected_version:
                raise CasConflict(path, expected_version, actual)
            if row is None:
                return
            row.deleted_at = _utcnow()
            row.updated_at = _utcnow()
            await db.commit()

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


