"""D10 matrix T7–T8 — TreeIO hydrate/sync on docker + e2b."""

from __future__ import annotations

from uuid import uuid4

import pytest

from supervisor.sandbox.e2b import E2BProvider
from supervisor.sandbox.local_docker import LocalDockerProvider
from supervisor.seams.providers.memory_object_storage import MemoryObjectStorage
from supervisor.seams.providers.memory_workspace_store import MemoryWorkspaceStore
from supervisor.seams.workspace_hydrator import StoreWorkspaceHydrator
from supervisor.seams.workspace_store import WorkspacePathEscape
from tests.unit.test_docker import FakeDockerClient
from tests.unit.test_e2b import FakeE2BClient


@pytest.mark.asyncio
async def test_t7_docker_treeio_hydrate_sync_destroy_roundtrip(fake_docker: FakeDockerClient) -> None:
    objects = MemoryObjectStorage()
    store = MemoryWorkspaceStore(objects=objects)
    scope = uuid4().hex
    await store.commit_blob(scope, "note.txt", b"from-durable")
    hydrator = StoreWorkspaceHydrator(store)
    provider = LocalDockerProvider(client=fake_docker, hydrator=hydrator)
    sb = await provider.create(image="sha256:" + "a" * 64)

    report = await sb.hydrate_workspace(scope)
    assert "note.txt" in report.written
    tree = sb.as_tree_io()
    await tree.write_bytes("note.txt", b"mutated")
    await sb.sync_workspace(scope)
    row = await store.stat(scope, "note.txt")
    assert row is not None
    assert await objects.get(row.object_key) == b"mutated"

    cid = sb.info.id
    await sb.destroy()
    sb2 = await provider.create(image="sha256:" + "a" * 64)
    assert sb2.info.id != cid
    await sb2.hydrate_workspace(scope)
    assert await sb2.as_tree_io().read_bytes("note.txt") == b"mutated"


@pytest.mark.asyncio
async def test_t7_e2b_treeio_hydrate_sync_roundtrip(fake_e2b: FakeE2BClient) -> None:
    objects = MemoryObjectStorage()
    store = MemoryWorkspaceStore(objects=objects)
    scope = uuid4().hex
    await store.commit_blob(scope, "e2b.txt", b"e2b-durable")
    hydrator = StoreWorkspaceHydrator(store)
    provider = E2BProvider(client=fake_e2b, hydrator=hydrator)
    sb = await provider.create(image="sha256:" + "b" * 64)

    report = await sb.hydrate_workspace(scope)
    assert "e2b.txt" in report.written
    await sb.as_tree_io().write_bytes("e2b.txt", b"e2b-mutated")
    await sb.sync_workspace(scope)
    row = await store.stat(scope, "e2b.txt")
    assert row is not None
    assert await objects.get(row.object_key) == b"e2b-mutated"


@pytest.mark.asyncio
async def test_t8_docker_treeio_rejects_path_escape(fake_docker: FakeDockerClient) -> None:
    provider = LocalDockerProvider(client=fake_docker)
    sb = await provider.create(image="sha256:" + "c" * 64)
    tree = sb.as_tree_io()
    with pytest.raises(WorkspacePathEscape):
        await tree.write_bytes("../escape.txt", b"x")


@pytest.mark.asyncio
async def test_t8_e2b_treeio_rejects_path_escape(fake_e2b: FakeE2BClient) -> None:
    provider = E2BProvider(client=fake_e2b)
    sb = await provider.create(image="sha256:" + "d" * 64)
    tree = sb.as_tree_io()
    with pytest.raises(WorkspacePathEscape):
        await tree.write_bytes("../escape.txt", b"x")
