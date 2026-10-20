"""Postgres-backed WorkspaceStore (Stage 1 / D9 durable metadata SoT)."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from supervisor.db.models import ScopeRow, WorkspaceFileRow
from supervisor.seams.providers.workspace.memory_workspace_store import normalize_workspace_path
from supervisor.seams.providers.workspace.sync import base_of, three_way_sync
from supervisor.seams.services.object_storage import ObjectStorage
from supervisor.seams.services.workspace_store import (
    CasConflict,
    ChecksumMismatch,
    HydrateIncomplete,
    WorkspaceFileRecord,
    WorkspaceSyncReport,
    WorkspaceTreeIO,
)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _object_key(digest: str) -> str:
    return f"blobs/{digest[:2]}/{digest}"


def _utcnow() -> datetime:
    return datetime.now(UTC)


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
    ) -> WorkspaceFileRecord:
        path = normalize_workspace_path(path)
        digest = _sha256(data)
        object_key = _object_key(digest)
        await self.objects.put(object_key, data, content_sha256=digest)

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

            if live is None:
                # Revive soft-deleted row in place if present (full-unique schema),
                # else insert. Partial-unique schema allows insert alongside tombstones.
                tomb = await db.scalar(
                    select(WorkspaceFileRow).where(
                        WorkspaceFileRow.scope_id == scope_id,
                        WorkspaceFileRow.path == path,
                        WorkspaceFileRow.deleted_at.is_not(None),
                    )
                )
                if tomb is not None:
                    tomb.content_sha256 = digest
                    tomb.object_key = object_key
                    tomb.size_bytes = len(data)
                    tomb.mime_type = mime_type
                    # Versions keep counting through a delete, so a version a
                    # session saw before can never name a different file.
                    tomb.version = int(tomb.version) + 1
                    tomb.deleted_at = None
                    tomb.updated_at = _utcnow()
                    await db.commit()
                    await db.refresh(tomb)
                    return _row_to_record(tomb)

                row = WorkspaceFileRow(
                    id=uuid4().hex,
                    scope_id=scope_id,
                    path=path,
                    content_sha256=digest,
                    object_key=object_key,
                    size_bytes=len(data),
                    mime_type=mime_type,
                    version=1,
                    deleted_at=None,
                )
                db.add(row)
                await db.commit()
                await db.refresh(row)
                return _row_to_record(row)

            live.content_sha256 = digest
            live.object_key = object_key
            live.size_bytes = len(data)
            live.mime_type = mime_type
            live.version = int(live.version) + 1
            live.updated_at = _utcnow()
            await db.commit()
            await db.refresh(live)
            return _row_to_record(live)

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


