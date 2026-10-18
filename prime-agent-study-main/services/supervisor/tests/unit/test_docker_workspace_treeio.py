"""Stage 5: Docker TreeIO hydrate/sync + path confinement."""

from __future__ import annotations

from uuid import uuid4

import pytest

from supervisor.sandbox.local_docker import LocalDockerProvider
from supervisor.seams.providers.memory_object_storage import MemoryObjectStorage
from supervisor.seams.providers.memory_workspace_store import MemoryWorkspaceStore
from supervisor.seams.workspace_hydrator import StoreWorkspaceHydrator
from supervisor.seams.workspace_store import WorkspacePathEscape
from tests.unit.test_docker import FakeDockerClient


@pytest.mark.asyncio
async def test_docker_treeio_hydrate_sync_roundtrip() -> None:
    objects = MemoryObjectStorage()
    store = MemoryWorkspaceStore(objects=objects)
    scope = uuid4().hex
    await store.commit_blob(scope, "note.txt", b"from-durable")
    hydrator = StoreWorkspaceHydrator(store)
    provider = LocalDockerProvider(client=FakeDockerClient(), hydrator=hydrator)
    sb = await provider.create(image="sha256:" + "a" * 64)

    report = await sb.hydrate_workspace(scope)
    assert "note.txt" in report.written
    tree = sb.as_tree_io()
    assert await tree.read_bytes("note.txt") == b"from-durable"

    await tree.write_bytes("note.txt", b"mutated")
    await tree.write_bytes("extra.txt", b"new")
    sync = await sb.sync_workspace(scope)
    assert "note.txt" in sync.written
    assert "extra.txt" in sync.written
    row = await store.stat(scope, "note.txt")
    assert row is not None
    assert await objects.get(row.object_key) == b"mutated"


@pytest.mark.asyncio
async def test_docker_treeio_rejects_path_escape() -> None:
    provider = LocalDockerProvider(client=FakeDockerClient())
    sb = await provider.create(image="sha256:" + "b" * 64)
    tree = sb.as_tree_io()
    with pytest.raises(WorkspacePathEscape):
        await tree.write_bytes("../escape.txt", b"x")
