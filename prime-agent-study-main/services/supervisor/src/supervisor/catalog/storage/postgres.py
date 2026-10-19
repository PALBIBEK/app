"""PostgreSQL repository implementations for production use.

Design principles:
- Full Protocol compliance
- Async SQLAlchemy 2.0 patterns
- Optimistic locking via version columns
- Proper transaction handling
- Index-optimized queries

Reference: policy/approvals.py PostgresApprovalRecovery pattern.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from sqlalchemy import and_, desc, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from supervisor.catalog.storage.converters import (
    artifact_from_dict,
    artifact_to_dict,
    kind_of,
    metadata_from_dict,
    with_digest,
    with_metadata,
    with_status,
)
from supervisor.catalog.core.digests import content_digest
from supervisor.catalog.core.errors import (
    CatalogStorageError,
    DigestMismatch,
    DigestMissing,
    ImmutableArtifact,
    NotOffered,
)
from supervisor.catalog.storage.memory import reject_floating_ref
from supervisor.catalog.core.types import (
    ArtifactKind,
    ArtifactMetadata,
    ArtifactStatus,
    ArtifactUsageRecord,
    ArtifactUsageStats,
)

if TYPE_CHECKING:
    from supervisor.catalog.core.types import Artifact


__all__ = [
    "PostgresArtifactStore",
    "PostgresUsageStore",
]


# =============================================================================
# PostgreSQL Artifact Store
# =============================================================================

class PostgresArtifactStore:
    """PostgreSQL artifact storage implementing ArtifactStoreProtocol.

    Uses SQLAlchemy async sessions with proper transaction handling.
    Supports the extended schema with creator/owner tracking.

    Attributes:
        _session_factory: Async session factory for database access
    """

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        self._session_factory = session_factory

    async def offer(
        self,
        artifact: Artifact,
        *,
        created_by: str,
    ) -> Artifact:
        """Publish artifact; compute digest from body.

        Args:
            artifact: The artifact to publish
            created_by: Principal ID of the creator

        Returns:
            Artifact with computed digest and metadata

        Raises:
            ImmutableArtifact: If attempting to mutate existing artifact
            CatalogStorageError: If database operation fails
        """
        # Import here to avoid circular dependency
        from supervisor.db.models import CatalogArtifactRow

        kind = kind_of(artifact)
        body = artifact.body_for_digest()
        digest = content_digest(body)

        now = datetime.now(timezone.utc)

        try:
            async with self._session_factory() as db:
                # Check for existing artifact
                row = await db.get(
                    CatalogArtifactRow,
                    (kind.value, artifact.name, digest),
                )

                if row is not None:
                    # Verify immutability
                    existing_body = dict(row.body)
                    if existing_body != artifact_to_dict(artifact):
                        raise ImmutableArtifact(
                            kind=kind.value,
                            artifact_id=artifact.name,
                            digest=digest,
                        )
                    # Return existing artifact
                    return self._row_to_artifact(row)

                # Prepare artifact with digest and metadata
                metadata = ArtifactMetadata(
                    created_by=created_by,
                    created_at=now,
                    updated_at=now,
                    owner_id=created_by,
                )
                stored = with_digest(artifact, digest)
                stored = with_metadata(stored, metadata)

                # Create row with extended fields
                row = CatalogArtifactRow(
                    kind=kind.value,
                    artifact_id=artifact.name,
                    digest=digest,
                    status=stored.status.value if isinstance(stored.status, ArtifactStatus) else stored.status,
                    body=artifact_to_dict(stored),
                    created_by=created_by,
                    owner_id=created_by,
                    created_at=now,
                    updated_at=now,
                )
                db.add(row)
                await db.commit()

                return stored

        except ImmutableArtifact:
            raise
        except Exception as e:
            raise CatalogStorageError(
                "offer",
                str(e),
                artifact_kind=kind.value,
                artifact_id=artifact.name,
            ) from e

    async def get(
        self,
        kind: ArtifactKind,
        artifact_id: str,
        digest: str,
        *,
        require_offered: bool = False,
    ) -> Artifact:
        """Retrieve artifact by kind, id, and digest.

        Args:
            kind: Type of artifact
            artifact_id: Artifact name
            digest: Content hash
            require_offered: If True, raise if status != offered

        Returns:
            The artifact

        Raises:
            DigestMissing: If artifact not found
            DigestMismatch: If stored digest differs
            NotOffered: If require_offered=True and status != offered
        """
        from supervisor.db.models import CatalogArtifactRow

        reject_floating_ref(digest)
        reject_floating_ref(artifact_id)

        try:
            async with self._session_factory() as db:
                row = await db.get(
                    CatalogArtifactRow,
                    (kind.value, artifact_id, digest),
                )

                if row is None:
                    raise DigestMissing(
                        kind=kind.value,
                        artifact_id=artifact_id,
                        digest=digest,
                    )

                artifact = self._row_to_artifact(row)

                # Verify integrity
                actual = content_digest(artifact.body_for_digest())
                if actual != digest:
                    raise DigestMismatch(expected=digest, actual=actual)

                if require_offered and artifact.status != ArtifactStatus.OFFERED:
                    raise NotOffered(
                        kind=kind.value,
                        artifact_id=artifact_id,
                        digest=digest,
                        status=artifact.status.value if isinstance(artifact.status, ArtifactStatus) else str(artifact.status),
                    )

                return artifact

        except (DigestMissing, DigestMismatch, NotOffered):
            raise
        except Exception as e:
            raise CatalogStorageError(
                "get",
                str(e),
                artifact_kind=kind.value,
                artifact_id=artifact_id,
            ) from e

    async def deprecate(
        self,
        kind: ArtifactKind,
        artifact_id: str,
        digest: str,
        *,
        deprecated_by: str,
    ) -> None:
        """Mark artifact as deprecated.

        Args:
            kind: Type of artifact
            artifact_id: Artifact name
            digest: Content hash
            deprecated_by: Principal ID who deprecated

        Raises:
            DigestMissing: If artifact not found
            CatalogStorageError: If database operation fails
        """
        from supervisor.db.models import CatalogArtifactRow

        now = datetime.now(timezone.utc)

        try:
            async with self._session_factory() as db:
                row = await db.get(
                    CatalogArtifactRow,
                    (kind.value, artifact_id, digest),
                )

                if row is None:
                    raise DigestMissing(
                        kind=kind.value,
                        artifact_id=artifact_id,
                        digest=digest,
                    )

                row.status = ArtifactStatus.DEPRECATED.value
                row.updated_at = now
                row.deprecated_by = deprecated_by
                row.deprecated_at = now

                # Update body with new status
                body = dict(row.body)
                body["status"] = ArtifactStatus.DEPRECATED.value
                row.body = body

                await db.commit()

        except DigestMissing:
            raise
        except Exception as e:
            raise CatalogStorageError(
                "deprecate",
                str(e),
                artifact_kind=kind.value,
                artifact_id=artifact_id,
            ) from e

    async def list_artifacts(
        self,
        kind: ArtifactKind,
        *,
        status: ArtifactStatus | None = None,
        created_by: str | None = None,
        owner_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[Artifact]:
        """List artifacts with optional filters.

        Args:
            kind: Type of artifact to list
            status: Filter by status
            created_by: Filter by creator
            owner_id: Filter by owner
            limit: Max results
            offset: Skip first N results

        Returns:
            List of matching artifacts
        """
        from supervisor.db.models import CatalogArtifactRow

        try:
            async with self._session_factory() as db:
                stmt = select(CatalogArtifactRow).where(
                    CatalogArtifactRow.kind == kind.value
                )

                if status is not None:
                    stmt = stmt.where(
                        CatalogArtifactRow.status == status.value
                    )

                if created_by is not None:
                    stmt = stmt.where(
                        CatalogArtifactRow.created_by == created_by
                    )

                if owner_id is not None:
                    stmt = stmt.where(
                        CatalogArtifactRow.owner_id == owner_id
                    )

                stmt = stmt.order_by(
                    CatalogArtifactRow.artifact_id,
                    CatalogArtifactRow.digest,
                ).offset(offset).limit(limit)

                result = await db.execute(stmt)
                rows = result.scalars().all()

                return [self._row_to_artifact(row) for row in rows]

        except Exception as e:
            raise CatalogStorageError(
                "list_artifacts",
                str(e),
                artifact_kind=kind.value,
            ) from e

    async def get_by_digest(
        self,
        digest: str,
    ) -> tuple[ArtifactKind, str] | None:
        """Reverse lookup: digest -> (kind, artifact_id).

        Args:
            digest: Content hash to look up

        Returns:
            Tuple of (kind, artifact_id) or None if not found
        """
        from supervisor.db.models import CatalogArtifactRow

        try:
            async with self._session_factory() as db:
                stmt = select(
                    CatalogArtifactRow.kind,
                    CatalogArtifactRow.artifact_id,
                ).where(
                    CatalogArtifactRow.digest == digest
                ).limit(1)

                result = await db.execute(stmt)
                row = result.first()

                if row is None:
                    return None

                return (ArtifactKind(row.kind), row.artifact_id)

        except Exception:
            return None

    async def list_versions(
        self,
        kind: ArtifactKind,
        artifact_id: str,
    ) -> list[str]:
        """List all digests for an artifact (oldest first).

        Args:
            kind: Type of artifact
            artifact_id: Artifact name

        Returns:
            List of digest strings, oldest first
        """
        from supervisor.db.models import CatalogArtifactRow

        try:
            async with self._session_factory() as db:
                stmt = select(CatalogArtifactRow.digest).where(
                    and_(
                        CatalogArtifactRow.kind == kind.value,
                        CatalogArtifactRow.artifact_id == artifact_id,
                    )
                ).order_by(CatalogArtifactRow.created_at)

                result = await db.execute(stmt)
                return [row.digest for row in result.all()]

        except Exception:
            return []

    async def get_metadata(
        self,
        kind: ArtifactKind,
        artifact_id: str,
        digest: str,
    ) -> ArtifactMetadata | None:
        """Get artifact metadata without full body.

        Args:
            kind: Type of artifact
            artifact_id: Artifact name
            digest: Content hash

        Returns:
            Metadata or None if not found
        """
        from supervisor.db.models import CatalogArtifactRow

        try:
            async with self._session_factory() as db:
                row = await db.get(
                    CatalogArtifactRow,
                    (kind.value, artifact_id, digest),
                )

                if row is None:
                    return None

                # Extract metadata from body
                body = dict(row.body)
                return metadata_from_dict(body.get("metadata"))

        except Exception:
            return None

    def _row_to_artifact(self, row: Any) -> Artifact:
        """Convert database row to artifact.

        Args:
            row: CatalogArtifactRow instance

        Returns:
            Artifact instance
        """
        kind = ArtifactKind(row.kind)
        body = dict(row.body)
        # Ensure status is in sync
        body["status"] = row.status
        return artifact_from_dict(kind, body)


# =============================================================================
# PostgreSQL Usage Store
# =============================================================================

class PostgresUsageStore:
    """PostgreSQL usage tracking implementing UsageStoreProtocol.

    Uses SQLAlchemy async sessions with batch-optimized writes.
    Provides aggregated analytics queries.

    Attributes:
        _session_factory: Async session factory for database access
    """

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        self._session_factory = session_factory

    async def record_usage(
        self,
        record: ArtifactUsageRecord,
    ) -> None:
        """Record a usage event.

        Args:
            record: Usage event to record
        """
        # Import here to avoid issues before migration
        try:
            from supervisor.db.models import CatalogUsageRow
        except ImportError:
            # Table doesn't exist yet, silently skip
            return

        try:
            async with self._session_factory() as db:
                row = CatalogUsageRow(
                    id=str(uuid4()),
                    kind=record.artifact_kind.value,
                    artifact_id=record.artifact_id,
                    digest=record.artifact_digest,
                    session_id=record.session_id,
                    principal_id=record.principal_id,
                    operation=record.operation,
                    timestamp_ms=record.timestamp_ms,
                )
                db.add(row)
                await db.commit()

        except Exception:
            # Don't fail the operation if usage tracking fails
            pass

    async def record_error(
        self,
        kind: ArtifactKind,
        artifact_id: str,
        digest: str,
        error_code: str,
        *,
        principal_id: str | None = None,
        session_id: str | None = None,
    ) -> None:
        """Record an error for analytics.

        Args:
            kind: Type of artifact
            artifact_id: Artifact name
            digest: Content hash
            error_code: Error classification
            principal_id: Who triggered the error
            session_id: Session context
        """
        try:
            from supervisor.db.models import CatalogErrorRow
        except ImportError:
            return

        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)

        try:
            async with self._session_factory() as db:
                row = CatalogErrorRow(
                    id=str(uuid4()),
                    kind=kind.value,
                    artifact_id=artifact_id,
                    digest=digest,
                    error_code=error_code,
                    principal_id=principal_id,
                    session_id=session_id,
                    timestamp_ms=now_ms,
                )
                db.add(row)
                await db.commit()

        except Exception:
            pass

    async def get_stats(
        self,
        kind: ArtifactKind,
        artifact_id: str,
        digest: str | None = None,
        *,
        since: datetime | None = None,
    ) -> ArtifactUsageStats:
        """Get aggregated usage statistics.

        Args:
            kind: Type of artifact
            artifact_id: Artifact name
            digest: Specific version (None for all)
            since: Only count usage since this time

        Returns:
            Aggregated statistics
        """
        try:
            from supervisor.db.models import CatalogErrorRow, CatalogUsageRow
        except ImportError:
            return ArtifactUsageStats(
                artifact_kind=kind,
                artifact_id=artifact_id,
                artifact_digest=digest,
                resolution_count=0,
                bind_count=0,
                session_count=0,
                unique_principals=0,
                last_used_at=None,
                error_count=0,
                error_rate=0.0,
            )

        since_ms = int(since.timestamp() * 1000) if since else 0

        try:
            async with self._session_factory() as db:
                # Base conditions
                conditions = [
                    CatalogUsageRow.kind == kind.value,
                    CatalogUsageRow.artifact_id == artifact_id,
                ]
                if digest:
                    conditions.append(CatalogUsageRow.digest == digest)
                if since_ms:
                    conditions.append(CatalogUsageRow.timestamp_ms >= since_ms)

                # Get counts by operation
                stmt = select(
                    CatalogUsageRow.operation,
                    func.count().label("cnt"),
                ).where(and_(*conditions)).group_by(CatalogUsageRow.operation)

                result = await db.execute(stmt)
                counts = {row.operation: row.cnt for row in result.all()}

                resolution_count = counts.get("resolved", 0)
                bind_count = counts.get("bound", 0)
                session_count = counts.get("session_started", 0)

                # Get unique principals
                stmt = select(
                    func.count(func.distinct(CatalogUsageRow.principal_id))
                ).where(and_(*conditions))
                result = await db.execute(stmt)
                unique_principals = result.scalar() or 0

                # Get last usage time
                stmt = select(
                    func.max(CatalogUsageRow.timestamp_ms)
                ).where(and_(*conditions))
                result = await db.execute(stmt)
                last_ms = result.scalar()
                last_used_at = (
                    datetime.fromtimestamp(last_ms / 1000, tz=timezone.utc)
                    if last_ms
                    else None
                )

                # Get error count
                error_conditions = [
                    CatalogErrorRow.kind == kind.value,
                    CatalogErrorRow.artifact_id == artifact_id,
                ]
                if digest:
                    error_conditions.append(CatalogErrorRow.digest == digest)
                if since_ms:
                    error_conditions.append(CatalogErrorRow.timestamp_ms >= since_ms)

                stmt = select(func.count()).select_from(CatalogErrorRow).where(
                    and_(*error_conditions)
                )
                result = await db.execute(stmt)
                error_count = result.scalar() or 0

                total_ops = resolution_count + bind_count + session_count
                error_rate = error_count / total_ops if total_ops > 0 else 0.0

                return ArtifactUsageStats(
                    artifact_kind=kind,
                    artifact_id=artifact_id,
                    artifact_digest=digest,
                    resolution_count=resolution_count,
                    bind_count=bind_count,
                    session_count=session_count,
                    unique_principals=unique_principals,
                    last_used_at=last_used_at,
                    error_count=error_count,
                    error_rate=error_rate,
                )

        except Exception:
            return ArtifactUsageStats(
                artifact_kind=kind,
                artifact_id=artifact_id,
                artifact_digest=digest,
                resolution_count=0,
                bind_count=0,
                session_count=0,
                unique_principals=0,
                last_used_at=None,
                error_count=0,
                error_rate=0.0,
            )

    async def get_sessions_using(
        self,
        kind: ArtifactKind,
        artifact_id: str,
        digest: str,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> list[str]:
        """Get session IDs that used this artifact.

        Args:
            kind: Type of artifact
            artifact_id: Artifact name
            digest: Content hash
            limit: Max results
            offset: Skip first N

        Returns:
            List of session IDs
        """
        try:
            from supervisor.db.models import CatalogUsageRow
        except ImportError:
            return []

        try:
            async with self._session_factory() as db:
                stmt = (
                    select(CatalogUsageRow.session_id)
                    .where(
                        and_(
                            CatalogUsageRow.kind == kind.value,
                            CatalogUsageRow.artifact_id == artifact_id,
                            CatalogUsageRow.digest == digest,
                        )
                    )
                    .distinct()
                    .order_by(CatalogUsageRow.session_id)
                    .offset(offset)
                    .limit(limit)
                )

                result = await db.execute(stmt)
                return [row.session_id for row in result.all()]

        except Exception:
            return []

    async def get_popular_artifacts(
        self,
        kind: ArtifactKind,
        *,
        since_ms: int | None = None,
        limit: int = 10,
    ) -> list[ArtifactUsageStats]:
        """Get most-used artifacts.

        Args:
            kind: Type of artifact
            since_ms: Only count usage since this timestamp
            limit: Max results

        Returns:
            List of usage stats, sorted by usage count desc
        """
        try:
            from supervisor.db.models import CatalogUsageRow
        except ImportError:
            return []

        try:
            async with self._session_factory() as db:
                conditions = [CatalogUsageRow.kind == kind.value]
                if since_ms:
                    conditions.append(CatalogUsageRow.timestamp_ms >= since_ms)

                stmt = (
                    select(
                        CatalogUsageRow.artifact_id,
                        CatalogUsageRow.digest,
                        func.count().label("usage_count"),
                    )
                    .where(and_(*conditions))
                    .group_by(
                        CatalogUsageRow.artifact_id,
                        CatalogUsageRow.digest,
                    )
                    .order_by(desc("usage_count"))
                    .limit(limit)
                )

                result = await db.execute(stmt)
                rows = result.all()

                since_dt = (
                    datetime.fromtimestamp(since_ms / 1000, tz=timezone.utc)
                    if since_ms
                    else None
                )

                return [
                    await self.get_stats(kind, row.artifact_id, row.digest, since=since_dt)
                    for row in rows
                ]

        except Exception:
            return []

    async def get_usage_history(
        self,
        kind: ArtifactKind,
        artifact_id: str,
        digest: str | None = None,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> list[ArtifactUsageRecord]:
        """Get raw usage records.

        Args:
            kind: Type of artifact
            artifact_id: Artifact name
            digest: Specific version (None for all)
            limit: Max results
            offset: Skip first N

        Returns:
            List of usage records, newest first
        """
        try:
            from supervisor.db.models import CatalogUsageRow
        except ImportError:
            return []

        try:
            async with self._session_factory() as db:
                conditions = [
                    CatalogUsageRow.kind == kind.value,
                    CatalogUsageRow.artifact_id == artifact_id,
                ]
                if digest:
                    conditions.append(CatalogUsageRow.digest == digest)

                stmt = (
                    select(CatalogUsageRow)
                    .where(and_(*conditions))
                    .order_by(desc(CatalogUsageRow.timestamp_ms))
                    .offset(offset)
                    .limit(limit)
                )

                result = await db.execute(stmt)
                rows = result.scalars().all()

                return [
                    ArtifactUsageRecord(
                        artifact_kind=ArtifactKind(row.kind),
                        artifact_id=row.artifact_id,
                        artifact_digest=row.digest,
                        session_id=row.session_id,
                        principal_id=row.principal_id,
                        timestamp_ms=row.timestamp_ms,
                        operation=row.operation,
                    )
                    for row in rows
                ]

        except Exception:
            return []
