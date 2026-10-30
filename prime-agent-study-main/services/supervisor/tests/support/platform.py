"""The shipped platform with only its edges mocked: sandbox provider and the D14 image."""

from __future__ import annotations

import dataclasses
import hashlib
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from supervisor.app import SupervisorState, build_supervisor
from supervisor.catalog.loading.bootstrap import (
    agent_platform_root,
    bootstrap_composition_catalog,
    default_catalog_dir,
)
from supervisor.catalog.repository import hydrate_composition_catalog
from supervisor.harness import HarnessCatalog, load_manifest
from supervisor.seams.services import WorkspaceStore
from tests.support.scripted import all_variants
from tests.support.session_env import TrackingProvider

MOCK_IMAGE = "sha256:" + hashlib.sha256(b"mock-githi-image").hexdigest()


def shipped_harness_catalog() -> HarnessCatalog:
    """Shipped manifests; sandboxed ones get a mock image in place of the D14 placeholder."""
    catalog = HarnessCatalog()
    for path in sorted((agent_platform_root() / "harness" / "versions").glob("*.yaml")):
        manifest = load_manifest(path.read_text(encoding="utf-8"))
        if manifest.requires_sandbox:
            manifest = dataclasses.replace(manifest, image_digest=MOCK_IMAGE)
        catalog.register_sync(manifest)
    return catalog


async def boot_platform(
    factory: async_sessionmaker[AsyncSession],
    blob_dir: Path,
    *,
    workspace_store: WorkspaceStore | None = None,
    virtual_key_client: object | None = None,
) -> tuple[SupervisorState, TrackingProvider]:
    """One supervisor process, as bootstrap_supervisor builds it (persisted catalog hydrated)."""
    compositions = bootstrap_composition_catalog(default_catalog_dir())
    await hydrate_composition_catalog(compositions, factory)
    sandboxes = TrackingProvider()
    state = build_supervisor(
        session_factory=factory,
        # Shipped manifests plus githi variants for approval, at-most-once and budget tests.
        catalog=all_variants(shipped_harness_catalog()),
        blob_dir=blob_dir,
        sandbox_provider=sandboxes,
        composition_catalog=compositions,
        workspace_store=workspace_store,
        virtual_key_client=virtual_key_client,
    )
    return state, sandboxes
