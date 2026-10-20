"""Durable persistence for the in-memory composition catalog.

The ``CompositionCatalog`` is the read path used during session creation. This
module mirrors publish/deprecate operations into the ``catalog_artifacts``
table and rebuilds the in-memory catalog from it on startup, so artifacts
published through the API survive a restart.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, TypedDict

from sqlalchemy import select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from supervisor.catalog.core.types import ArtifactKind, ArtifactStatus
from supervisor.catalog.storage import CompositionCatalog
from supervisor.catalog.storage.composition import SYSTEM_OWNER
from supervisor.catalog.storage.converters import artifact_from_dict, artifact_to_dict
from supervisor.db.models import CatalogArtifactRow

__all__ = [
    "artifact_summary",
    "hydrate_composition_catalog",
    "persist_deprecate",
    "persist_offer",
    "sync_composition",
]

# Referenced artifacts must be offered before the compositions that pin them.
_HYDRATE_ORDER: dict[str, int] = {
    ArtifactKind.PROMPT_PACK.value: 0,
    ArtifactKind.TOOL_SET.value: 0,
    ArtifactKind.SANDBOX_PROFILE.value: 0,
    ArtifactKind.MEMORY_PROFILE.value: 0,
    ArtifactKind.COMPOSITION.value: 1,
}


_EPOCH = datetime.min.replace(tzinfo=UTC)


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


async def persisted_owner(
    factory: async_sessionmaker[AsyncSession], kind: ArtifactKind | str, artifact_id: str
) -> str | None:
    """The tenant that owns ``artifact_id`` according to the database, if it was ever published.

    Every replica decides ownership from here, so a stale in-memory catalog on
    one process cannot hand a name to a second tenant.
    """
    async with factory() as db:
        owner = (
            await db.execute(
                select(CatalogArtifactRow.owner_id)
                .where(
                    CatalogArtifactRow.kind == _kind_str(kind),
                    CatalogArtifactRow.artifact_id == artifact_id,
                )
                .limit(1)
            )
        ).scalar_one_or_none()
    return owner


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
        row.deprecated_at = datetime.now(UTC)


async def hydrate_composition_catalog(
    catalog: CompositionCatalog,
    factory: async_sessionmaker[AsyncSession],
) -> int:
    """Load every persisted artifact into ``catalog``. Returns the number loaded."""
    async with factory() as db:
        rows = (await db.execute(select(CatalogArtifactRow))).scalars().all()

    rows = sorted(rows, key=lambda r: (_HYDRATE_ORDER.get(r.kind, 1), r.created_at or _EPOCH))
    for row in rows:
        _reconcile(catalog, row)
    return len(rows)


_REFERENCES: tuple[tuple[str, str], ...] = (
    ("prompt_pack", ArtifactKind.PROMPT_PACK.value),
    ("tool_set", ArtifactKind.TOOL_SET.value),
    ("sandbox_profile", ArtifactKind.SANDBOX_PROFILE.value),
    ("memory_profile", ArtifactKind.MEMORY_PROFILE.value),
)
"""Composition body field → kind of the artifact it pins."""


async def sync_composition(
    catalog: CompositionCatalog,
    factory: async_sessionmaker[AsyncSession],
    composition_id: str,
    digest: str | None = None,
) -> None:
    """Bring one composition, and every artifact it pins, up to date from the database.

    The database is the catalog's source of truth; each process's in-memory
    catalog is a cache of it. Another process (an API replica, or the API when
    this is a worker) may have published or deprecated an artifact since this
    process hydrated, so anything about to resolve a session's pins syncs them
    first: missing artifacts are loaded, and statuses follow the database.
    """
    async with factory() as db:
        query = select(CatalogArtifactRow).where(
            CatalogArtifactRow.kind == ArtifactKind.COMPOSITION.value,
            CatalogArtifactRow.artifact_id == composition_id,
        )
        if digest is not None:
            query = query.where(CatalogArtifactRow.digest == digest)
        compositions = list((await db.execute(query)).scalars())
        pinned = {
            (kind, ref["id"], ref["digest"])
            for row in compositions
            for field, kind in _REFERENCES
            if isinstance(ref := row.body.get(field), dict) and ref.get("id") and ref.get("digest")
        } | {
            (ArtifactKind.SANDBOX_PROFILE.value, ref["id"], ref["digest"])
            for row in compositions
            for ref in (row.body.get("hands") or {}).values()
            if isinstance(ref, dict) and ref.get("id") and ref.get("digest")
        }
        references = (
            list(
                (
                    await db.execute(
                        select(CatalogArtifactRow).where(
                            tuple_(
                                CatalogArtifactRow.kind,
                                CatalogArtifactRow.artifact_id,
                                CatalogArtifactRow.digest,
                            ).in_(sorted(pinned))
                        )
                    )
                ).scalars()
            )
            if pinned
            else []
        )
    for row in (*references, *compositions):  # what a composition pins, before it
        _reconcile(catalog, row)


def _reconcile(catalog: CompositionCatalog, row: CatalogArtifactRow) -> None:
    """Make the cached artifact match its database row (content is digest-addressed)."""
    offered = catalog.offer(
        artifact_from_dict(row.kind, dict(row.body)), owner=row.owner_id or SYSTEM_OWNER
    )
    if row.status == ArtifactStatus.DEPRECATED.value:
        catalog.deprecate(row.kind, offered.name, offered.digest)


class ArtifactSummary(TypedDict):
    artifact_id: str
    digest: str
    status: str
    semver: str


def artifact_summary(artifact: Any) -> ArtifactSummary:
    """Fields shared by every artifact kind, shaped for ``CatalogArtifactOut``."""
    return {
        "artifact_id": artifact.name,
        "digest": artifact.digest,
        "status": _status_str(artifact.status),
        "semver": getattr(artifact, "semver", "") or "",
    }
