"""Durable persistence for the in-memory composition catalog.

The ``CompositionCatalog`` is the read path used during session creation. This
module mirrors publish/deprecate operations into the ``catalog_artifacts``
table and rebuilds the in-memory catalog from it on startup, so artifacts
published through the API survive a restart.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from supervisor.catalog.core.types import ArtifactKind, ArtifactStatus
from supervisor.catalog.storage import CompositionCatalog
from supervisor.catalog.storage.converters import artifact_from_dict, artifact_to_dict
from supervisor.db.models import CatalogArtifactRow

__all__ = ["artifact_summary", "hydrate_composition_catalog", "persist_deprecate", "persist_offer"]

# Referenced artifacts must be offered before the compositions that pin them.
_HYDRATE_ORDER: dict[str, int] = {
    ArtifactKind.PROMPT_PACK.value: 0,
    ArtifactKind.TOOL_SET.value: 0,
    ArtifactKind.SANDBOX_PROFILE.value: 0,
    ArtifactKind.MEMORY_PROFILE.value: 0,
    ArtifactKind.COMPOSITION.value: 1,
}


_EPOCH = datetime.min.replace(tzinfo=timezone.utc)


def _kind_str(kind: ArtifactKind | str) -> str:
    return kind.value if isinstance(kind, ArtifactKind) else str(kind)


def _status_str(status: Any) -> str:
    return status.value if hasattr(status, "value") else str(status)


async def persist_offer(
    factory: async_sessionmaker[AsyncSession],
    catalog: CompositionCatalog,
    kind: ArtifactKind | str,
    artifact_id: str,
    digest: str,
    *,
    created_by: str = "system",
) -> None:
    """Write (or re-offer) one catalog artifact to the database."""
    kind_s = _kind_str(kind)
    artifact = catalog.get(kind_s, artifact_id, digest)
    body = artifact_to_dict(artifact)
    async with factory() as db, db.begin():
        row = await db.get(CatalogArtifactRow, (kind_s, artifact_id, digest))
        if row is None:
            db.add(
                CatalogArtifactRow(
                    kind=kind_s,
                    artifact_id=artifact_id,
                    digest=digest,
                    status=ArtifactStatus.OFFERED.value,
                    body=body,
                    created_by=created_by,
                    owner_id=created_by,
                )
            )
        else:
            # Content is digest-addressed, so the body cannot change; only status can.
            row.status = ArtifactStatus.OFFERED.value
            row.deprecated_by = None
            row.deprecated_at = None


async def persist_deprecate(
    factory: async_sessionmaker[AsyncSession],
    kind: ArtifactKind | str,
    artifact_id: str,
    digest: str,
    *,
    deprecated_by: str = "system",
) -> None:
    """Mark a persisted artifact deprecated. No-op if it was never persisted."""
    async with factory() as db, db.begin():
        row = await db.get(CatalogArtifactRow, (_kind_str(kind), artifact_id, digest))
        if row is None:
            return
        row.status = ArtifactStatus.DEPRECATED.value
        row.deprecated_by = deprecated_by
        row.deprecated_at = datetime.now(timezone.utc)


async def hydrate_composition_catalog(
    catalog: CompositionCatalog,
    factory: async_sessionmaker[AsyncSession],
) -> int:
    """Load every persisted artifact into ``catalog``. Returns the number loaded."""
    async with factory() as db:
        rows = (await db.execute(select(CatalogArtifactRow))).scalars().all()

    rows = sorted(rows, key=lambda r: (_HYDRATE_ORDER.get(r.kind, 1), r.created_at or _EPOCH))
    for row in rows:
        artifact = artifact_from_dict(row.kind, dict(row.body))
        offered = catalog.offer(artifact)
        if row.status == ArtifactStatus.DEPRECATED.value:
            catalog.deprecate(row.kind, offered.name, offered.digest)
    return len(rows)


def artifact_summary(artifact: Any) -> dict[str, str]:
    """Fields shared by every artifact kind, shaped for ``CatalogArtifactOut``."""
    return {
        "artifact_id": artifact.name,
        "digest": artifact.digest,
        "status": _status_str(artifact.status),
        "semver": getattr(artifact, "semver", "") or "",
    }
