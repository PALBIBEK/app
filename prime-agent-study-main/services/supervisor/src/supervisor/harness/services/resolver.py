"""Version string to frozen harness manifest resolver.

Thread-safe async implementation with proper locking and logging.
Supports multiple resolution sources: DB, catalog, directory.

Reference: Original resolver.py refactored with asyncio.Lock.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from supervisor.harness.core.enums import HarnessStatus
from supervisor.harness.core.errors import HarnessNotOffered, UnknownHarness
from supervisor.harness.core.types import HarnessManifest
from supervisor.harness.storage.converters import (
    load_manifest,
    manifest_from_dict,
    manifest_to_dict,
)

if TYPE_CHECKING:
    from supervisor.db.models import HarnessVersionRow

__all__ = [
    "HarnessCatalog",
    "HarnessResolver",
]

logger = logging.getLogger(__name__)


def _reject_latest(version: str) -> None:
    """Reject 'latest' version strings.

    Args:
        version: Version string to check

    Raises:
        UnknownHarness: If version is 'latest' or ends with ':latest'
    """
    v = version.strip().lower()
    if v == "latest" or v.endswith(":latest"):
        raise UnknownHarness(version)


class HarnessCatalog:
    """In-memory catalog with thread-safe operations.

    Used for testing and as a fallback when no DB is configured.
    """

    def __init__(
        self,
        versions: dict[str, HarnessManifest] | None = None,
        *,
        status: dict[str, HarnessStatus] | None = None,
    ) -> None:
        """Initialize catalog.

        Args:
            versions: Initial version mappings
            status: Initial status mappings
        """
        self._lock = asyncio.Lock()
        self._versions: dict[str, HarnessManifest] = {}
        self._status: dict[str, HarnessStatus] = {}

        # Register initial versions
        if versions:
            for key, manifest in versions.items():
                st = (status or {}).get(key, HarnessStatus.ACTIVE)
                self._versions[manifest.harness_version] = manifest
                self._status[manifest.harness_version] = st

        logger.debug(
            "Initialized HarnessCatalog",
            extra={"version_count": len(self._versions)},
        )

    async def register(
        self,
        manifest: HarnessManifest,
        *,
        status: HarnessStatus = HarnessStatus.ACTIVE,
    ) -> None:
        """Register a harness manifest.

        Args:
            manifest: Manifest to register
            status: Initial status
        """
        async with self._lock:
            self._versions[manifest.harness_version] = manifest
            self._status[manifest.harness_version] = status
            logger.info(
                "Registered harness version",
                extra={
                    "harness_version": manifest.harness_version,
                    "status": status.value,
                },
            )

    def register_sync(
        self,
        manifest: HarnessManifest,
        *,
        status: HarnessStatus = HarnessStatus.ACTIVE,
    ) -> None:
        """Register synchronously (for initialization).

        Args:
            manifest: Manifest to register
            status: Initial status
        """
        self._versions[manifest.harness_version] = manifest
        self._status[manifest.harness_version] = status

    async def deprecate(self, version: str) -> None:
        """Mark a version as deprecated.

        Args:
            version: Version to deprecate

        Raises:
            UnknownHarness: If version not found
        """
        async with self._lock:
            if version not in self._versions:
                raise UnknownHarness(version)
            self._status[version] = HarnessStatus.DEPRECATED
            logger.info(
                "Deprecated harness version",
                extra={"harness_version": version},
            )

    async def resolve(
        self,
        version: str,
        *,
        for_create: bool = False,
    ) -> HarnessManifest:
        """Resolve version to manifest.

        Args:
            version: Version string
            for_create: If True, require active status

        Returns:
            Frozen manifest

        Raises:
            UnknownHarness: If version not found
            HarnessNotOffered: If for_create and status != active
        """
        _reject_latest(version)

        async with self._lock:
            manifest = self._versions.get(version)
            if manifest is None:
                raise UnknownHarness(version)

            status = self._status.get(version, HarnessStatus.ACTIVE)
            if for_create and status != HarnessStatus.ACTIVE:
                raise HarnessNotOffered(
                    harness_version=version,
                    status=status.value,
                )

            return manifest

    def resolve_sync(
        self,
        version: str,
        *,
        for_create: bool = False,
    ) -> HarnessManifest:
        """Resolve synchronously (for non-async contexts).

        Args:
            version: Version string
            for_create: If True, require active status

        Returns:
            Frozen manifest

        Raises:
            UnknownHarness: If version not found
            HarnessNotOffered: If for_create and status != active
        """
        _reject_latest(version)

        manifest = self._versions.get(version)
        if manifest is None:
            raise UnknownHarness(version)

        status = self._status.get(version, HarnessStatus.ACTIVE)
        if for_create and status != HarnessStatus.ACTIVE:
            raise HarnessNotOffered(
                harness_version=version,
                status=status.value,
            )

        return manifest

    async def list_available(
        self,
        *,
        include_deprecated: bool = False,
    ) -> list[str]:
        """List available versions.

        Args:
            include_deprecated: Include deprecated versions

        Returns:
            Sorted list of version strings
        """
        async with self._lock:
            out = []
            for v, st in self._status.items():
                if st == HarnessStatus.ACTIVE:
                    out.append(v)
                elif include_deprecated and st == HarnessStatus.DEPRECATED:
                    out.append(v)
            return sorted(out)

    async def get_status(self, version: str) -> HarnessStatus:
        """Get status of a version.

        Args:
            version: Version string

        Returns:
            Status enum value
        """
        async with self._lock:
            return self._status.get(version, HarnessStatus.ACTIVE)


class HarnessResolver:
    """Directory + table backed resolver with thread-safe cache.

    Supports multiple resolution sources with fallback:
    1. Thread-safe memory cache
    2. Database (HarnessVersionRow table)
    3. In-memory catalog
    4. Directory (YAML files)
    """

    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession] | None = None,
        versions_dir: Path | str | None = None,
        catalog: HarnessCatalog | None = None,
    ) -> None:
        """Initialize resolver.

        Args:
            session_factory: SQLAlchemy async session factory
            versions_dir: Directory containing version YAML files
            catalog: In-memory catalog for fallback
        """
        self._db = session_factory
        self._dir = Path(versions_dir) if versions_dir else None
        self._catalog = catalog or HarnessCatalog()
        self._cache: dict[str, HarnessManifest] = {}
        self._cache_lock = asyncio.Lock()
        self._io_calls = 0  # Test hook for verifying caching

        logger.debug(
            "Initialized HarnessResolver",
            extra={
                "has_db": self._db is not None,
                "versions_dir": str(self._dir) if self._dir else None,
            },
        )

    @property
    def catalog(self) -> HarnessCatalog:
        """Get the underlying catalog."""
        return self._catalog

    async def resolve(
        self,
        harness_version: str,
        *,
        for_create: bool = False,
    ) -> HarnessManifest:
        """Resolve version string to frozen manifest.

        Uses thread-safe cache to avoid redundant lookups.

        Args:
            harness_version: Exact version string (never "latest")
            for_create: If True, require active status

        Returns:
            Frozen manifest

        Raises:
            UnknownHarness: If version not found
            HarnessNotOffered: If for_create and status != active
        """
        _reject_latest(harness_version)

        # Check cache first (thread-safe)
        async with self._cache_lock:
            if harness_version in self._cache and not for_create:
                logger.debug(
                    "Cache hit for harness version",
                    extra={"harness_version": harness_version},
                )
                return self._cache[harness_version]

        # Try DB
        if self._db is not None:
            manifest = await self._resolve_from_db(harness_version, for_create)
            if manifest is not None:
                return manifest

        # Try in-memory catalog
        try:
            return await self._catalog.resolve(harness_version, for_create=for_create)
        except UnknownHarness:
            pass

        # Try directory
        if self._dir is not None:
            manifest = await self._resolve_from_dir(harness_version)
            if manifest is not None:
                return manifest

        raise UnknownHarness(harness_version)

    async def _resolve_from_db(
        self,
        harness_version: str,
        for_create: bool,
    ) -> HarnessManifest | None:
        """Resolve from database.

        Args:
            harness_version: Version string
            for_create: If True, require active status

        Returns:
            Manifest or None if not found
        """
        from supervisor.db.models import HarnessVersionRow

        async with self._db() as db:
            row = await db.get(HarnessVersionRow, harness_version)
            if row is None:
                return None

            self._io_calls += 1
            logger.debug(
                "Resolved harness version from DB",
                extra={
                    "harness_version": harness_version,
                    "status": row.status,
                },
            )

            if for_create and row.status != "active":
                raise HarnessNotOffered(
                    harness_version=harness_version,
                    status=row.status,
                )

            manifest = manifest_from_dict(dict(row.manifest))

            # Cache the result (thread-safe)
            async with self._cache_lock:
                self._cache[harness_version] = manifest

            return manifest

    async def _resolve_from_dir(
        self,
        harness_version: str,
    ) -> HarnessManifest | None:
        """Resolve from directory.

        Args:
            harness_version: Version string

        Returns:
            Manifest or None if not found
        """
        if self._dir is None:
            return None

        # Try .yaml extension first, then .yml
        for ext in (".yaml", ".yml"):
            path = self._dir / f"{harness_version}{ext}"
            if path.is_file():
                self._io_calls += 1
                logger.debug(
                    "Resolved harness version from file",
                    extra={
                        "harness_version": harness_version,
                        "path": str(path),
                    },
                )

                manifest = load_manifest(path.read_text())

                # Cache the result (thread-safe)
                async with self._cache_lock:
                    self._cache[harness_version] = manifest

                return manifest

        return None

    async def list_available(
        self,
        *,
        include_deprecated: bool = False,
    ) -> list[str]:
        """List available harness versions.

        Args:
            include_deprecated: Include deprecated versions

        Returns:
            Sorted list of version strings
        """
        if self._db is not None:
            from supervisor.db.models import HarnessVersionRow

            async with self._db() as db:
                if include_deprecated:
                    query = select(HarnessVersionRow).where(
                        HarnessVersionRow.status.in_(["active", "deprecated"])
                    )
                else:
                    query = select(HarnessVersionRow).where(
                        HarnessVersionRow.status == "active"
                    )
                rows = (await db.execute(query)).scalars().all()
                return sorted(r.harness_version for r in rows)

        return await self._catalog.list_available(include_deprecated=include_deprecated)

    async def publish(
        self,
        manifest: HarnessManifest,
        *,
        status: str = "active",
    ) -> None:
        """Publish a new harness version.

        Args:
            manifest: Manifest to publish
            status: Initial status (active/draft)
        """
        if self._db is None:
            await self._catalog.register(manifest, status=HarnessStatus(status))
            async with self._cache_lock:
                self._cache[manifest.harness_version] = manifest
            return

        from supervisor.db.models import HarnessVersionRow

        payload = manifest_to_dict(manifest)

        async with self._db() as db:
            row = await db.get(HarnessVersionRow, manifest.harness_version)
            if row is None:
                db.add(
                    HarnessVersionRow(
                        harness_version=manifest.harness_version,
                        protocol_version=manifest.protocol_version,
                        runtime=manifest.runtime,
                        manifest=payload,
                        status=status,
                    )
                )
                logger.info(
                    "Published new harness version",
                    extra={
                        "harness_version": manifest.harness_version,
                        "status": status,
                    },
                )
            else:
                # Never mutate an already-published manifest body - status only
                row.status = status
                logger.info(
                    "Updated harness version status",
                    extra={
                        "harness_version": manifest.harness_version,
                        "status": status,
                    },
                )
            await db.commit()

        async with self._cache_lock:
            self._cache[manifest.harness_version] = manifest

        await self._catalog.register(manifest, status=HarnessStatus(status))

    async def set_status(
        self,
        harness_version: str,
        status: str,
    ) -> None:
        """Update status of an existing version.

        Args:
            harness_version: Version to update
            status: New status

        Raises:
            UnknownHarness: If version not found
        """
        if self._db is not None:
            from supervisor.db.models import HarnessVersionRow

            async with self._db() as db:
                row = await db.get(HarnessVersionRow, harness_version)
                if row is None:
                    raise UnknownHarness(harness_version)
                row.status = status
                await db.commit()
                logger.info(
                    "Updated harness version status",
                    extra={
                        "harness_version": harness_version,
                        "status": status,
                    },
                )

        # Update catalog status
        async with self._catalog._lock:
            self._catalog._status[harness_version] = HarnessStatus(status)
