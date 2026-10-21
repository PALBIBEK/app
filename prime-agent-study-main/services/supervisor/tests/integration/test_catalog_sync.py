"""The catalog is shared through the database, not through a process's memory.

In production the API publishes and a separate worker process creates and
resumes sessions. Each holds its own in-memory catalog, hydrated at boot, so
what one publishes or deprecates must reach the other through the database.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from supervisor.app import SupervisorState
from supervisor.catalog.core.errors import NotOffered
from supervisor.catalog.repository import persist_deprecate, persist_offer
from supervisor.db.engine import open_db
from tests.support.platform import boot_platform


async def _process(factory: async_sessionmaker[AsyncSession], blobs: Path) -> SupervisorState:
    state, _ = await boot_platform(factory, blobs)
    return state


async def _publish_variant(api: SupervisorState, name: str) -> str:
    """What POST /v1/catalog/composition/publish does: offer in memory, persist."""
    catalog = api.composition_catalog
    assert catalog is not None
    base = catalog.get(
        "composition", "githi-v1", catalog.list_versions("composition", "githi-v1")[-1]
    )
    offered = catalog.offer(dataclasses.replace(base, name=name, digest=""))
    await persist_offer(api.manager._db, catalog, "composition", offered.name, offered.digest)
    return offered.digest


@pytest.fixture
async def processes(tmp_path: Path) -> tuple[SupervisorState, SupervisorState]:
    async with open_db(f"sqlite+aiosqlite:///{tmp_path / 'shared.db'}") as (_, factory):
        worker = await _process(factory, tmp_path / "blobs")  # booted before the publish
        api = await _process(factory, tmp_path / "blobs")
        yield api, worker


async def test_a_worker_creates_sessions_from_compositions_published_after_it_booted(
    processes: tuple[SupervisorState, SupervisorState],
) -> None:
    api, worker = processes
    digest = await _publish_variant(api, "published-later")
    sid = await worker.manager.create(
        tenant_id="t1",
        harness_version="githi-v1",
        composition_id="published-later",
        composition_digest=digest,
    )
    assert worker.manager._registry.get(sid) is not None


async def test_a_deprecation_reaches_the_worker(
    processes: tuple[SupervisorState, SupervisorState],
) -> None:
    api, worker = processes
    digest = await _publish_variant(api, "short-lived")
    create = {
        "tenant_id": "t1",
        "harness_version": "githi-v1",
        "composition_id": "short-lived",
        "composition_digest": digest,
    }
    await worker.manager.create(**create)  # the worker now caches it as offered

    await persist_deprecate(api.manager._db, "composition", "short-lived", digest)
    with pytest.raises(NotOffered):
        await worker.manager.create(**create)


async def test_a_worker_adopts_a_session_pinned_to_a_composition_published_after_it_booted(
    processes: tuple[SupervisorState, SupervisorState],
) -> None:
    api, worker = processes
    digest = await _publish_variant(api, "adopted")
    sid = await api.manager.create(
        tenant_id="t1",
        harness_version="githi-v1",
        composition_id="adopted",
        composition_digest=digest,
    )
    await worker.manager.resume(sid)  # the session's first worker is gone; this one adopts it
    assert worker.manager._registry.get(sid) is not None


async def test_a_worker_thinks_for_a_session_whose_sandbox_profile_it_has_never_seen(
    processes: tuple[SupervisorState, SupervisorState],
) -> None:
    """A model step runs on any worker. One booted before the session's composition
    and sandbox profile were published still builds the session's brain: the pins
    are synced before anything resolves them (the sandbox profile is read first)."""
    from supervisor.catalog.core.types import DigestRef, SandboxProviderName

    api, worker = processes
    catalog = api.composition_catalog
    assert catalog is not None
    base_profile = catalog.get(
        "sandbox_profile",
        "githi-docker-v1",
        catalog.list_versions("sandbox_profile", "githi-docker-v1")[-1],
    )
    profile = catalog.offer(
        dataclasses.replace(
            base_profile,
            name="sb-published-later",
            digest="",
            provider=SandboxProviderName.LOCAL_PROCESS,
        )
    )
    await persist_offer(api.manager._db, catalog, "sandbox_profile", profile.name, profile.digest)
    base = catalog.get(
        "composition", "githi-v1", catalog.list_versions("composition", "githi-v1")[-1]
    )
    composition = catalog.offer(
        dataclasses.replace(
            base,
            name="thinks-anywhere",
            digest="",
            sandbox_profile=DigestRef(id=profile.name, digest=profile.digest),
        )
    )
    await persist_offer(
        api.manager._db, catalog, "composition", composition.name, composition.digest
    )
    sid = await api.manager.create(
        tenant_id="t1",
        harness_version="githi-v1",
        composition_id=composition.name,
        composition_digest=composition.digest,
    )

    brain = await worker.manager.brain(sid)  # NotOffered before the fix

    assert worker.manager._registry.get(sid) is None, "a brain, not an adopted session"
    assert brain.seams is not None
