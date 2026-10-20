"""Harness versions registered through the API: persisted, owned, and shared across processes.

Shipped versions come from ``HARNESS_VERSIONS_DIR`` at boot and belong to no
tenant. A version a tenant registers is stored in ``harness_versions`` with
that tenant as owner, and every process reads it from there: the API that
registered it, and any worker that later creates or resumes a session on it
(``sync_harness_version``), as the composition catalog does.

A registered manifest is immutable: registering the same version again is a
no-op with the same body and a conflict with a different one.
"""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from supervisor.db.models import HarnessVersionRow
from supervisor.harness.core.errors import HarnessError
from supervisor.harness.core.types import HarnessManifest
from supervisor.harness.services.resolver import HarnessCatalog, HarnessStatus
from supervisor.harness.storage.converters import manifest_from_dict, manifest_to_dict

logger = logging.getLogger(__name__)

__all__ = [
    "HarnessVersionConflict",
    "deprecate_harness_version",
    "harness_owner",
    "hydrate_harness_catalog",
    "persist_harness_version",
    "sync_harness_version",
    "visible_to",
]


class HarnessVersionConflict(Exception):
    """The version id is taken: shipped, another tenant's, or registered with another body."""

    def __init__(self, harness_version: str, reason: str) -> None:
        super().__init__(f"harness version {harness_version!r} {reason}")
        self.harness_version = harness_version


def visible_to(owner: str | None, tenant_id: str) -> bool:
    """A shipped version (no owner) is everyone's; a registered one only its tenant's."""
    return owner is None or owner == tenant_id


async def harness_owner(
    factory: async_sessionmaker[AsyncSession], harness_version: str
) -> str | None:
    async with factory() as db:
        row = await db.get(HarnessVersionRow, harness_version)
        return row.owner_id if row is not None else None


async def persist_harness_version(
    factory: async_sessionmaker[AsyncSession],
    catalog: HarnessCatalog,
    manifest: HarnessManifest,
    *,
    owner: str,
) -> bool:
    """Register ``manifest`` for ``owner``; returns False when it was already registered as is."""
    version = manifest.harness_version
    body = manifest_to_dict(manifest)
    if catalog.has(version) and await _row(factory, version) is None:
        raise HarnessVersionConflict(version, "is a shipped version")  # on disk, never in the table
    async with factory() as db, db.begin():
        row = await db.get(HarnessVersionRow, version)
        if row is not None:
            if row.owner_id != owner:
                raise HarnessVersionConflict(version, "is already registered")
            if row.manifest != body:
                raise HarnessVersionConflict(version, "is registered with a different manifest")
            created = False
        else:
            db.add(
                HarnessVersionRow(
                    harness_version=version,
                    protocol_version=manifest.protocol_version,
                    runtime=manifest.runtime,
                    manifest=body,
                    status=HarnessStatus.ACTIVE.value,
                    owner_id=owner,
                )
            )
            created = True
    catalog.register_sync(manifest)
    return created


async def deprecate_harness_version(
    factory: async_sessionmaker[AsyncSession], catalog: HarnessCatalog, harness_version: str
) -> None:
    async with factory() as db, db.begin():
        row = await db.get(HarnessVersionRow, harness_version)
        if row is not None:
            row.status = HarnessStatus.DEPRECATED.value
    await catalog.deprecate(harness_version)


async def sync_harness_version(
    catalog: HarnessCatalog, factory: async_sessionmaker[AsyncSession], harness_version: str
) -> None:
    """Make this process's catalog match the database for one version (registered elsewhere)."""
    row = await _row(factory, harness_version)
    if row is not None:
        _reconcile(catalog, row)


async def hydrate_harness_catalog(
    catalog: HarnessCatalog, factory: async_sessionmaker[AsyncSession]
) -> int:
    """Load every registered version into ``catalog`` at boot; returns how many."""
    async with factory() as db:
        rows = (await db.execute(select(HarnessVersionRow))).scalars().all()
    loaded = 0
    for row in rows:
        try:
            _reconcile(catalog, row)
        except HarnessError as exc:
            # One version this build cannot run (a retired runtime) must not stop boot.
            logger.warning("harness version %s not loaded: %s", row.harness_version, exc)
            continue
        loaded += 1
    return loaded


async def _row(
    factory: async_sessionmaker[AsyncSession], harness_version: str
) -> HarnessVersionRow | None:
    async with factory() as db:
        return await db.get(HarnessVersionRow, harness_version)


def _reconcile(catalog: HarnessCatalog, row: HarnessVersionRow) -> None:
    catalog.register_sync(manifest_from_dict(dict(row.manifest)), status=HarnessStatus(row.status))
