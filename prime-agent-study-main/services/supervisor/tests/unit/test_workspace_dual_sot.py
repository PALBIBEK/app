"""WP-M0: dual-SoT contract tests (fakes only — no Docker / Seaweed)."""

from __future__ import annotations

import pytest

from supervisor.seams.object_storage import ObjectStorage, ObjectTransient
from supervisor.seams.providers.memory_object_storage import MemoryObjectStorage
from supervisor.seams.providers.memory_workspace_store import (
    MemoryTreeIO,
    MemoryWorkspaceStore,
    normalize_workspace_path,
)
from supervisor.seams.workspace_store import CasConflict, WorkspacePathEscape, WorkspaceStore


@pytest.fixture
def objects() -> MemoryObjectStorage:
    return MemoryObjectStorage()


@pytest.fixture
def store(objects: MemoryObjectStorage) -> MemoryWorkspaceStore:
    return MemoryWorkspaceStore(objects=objects)


def test_protocols_runtime_checkable(objects: MemoryObjectStorage, store: MemoryWorkspaceStore) -> None:
    assert isinstance(objects, ObjectStorage)
    assert isinstance(store, WorkspaceStore)


@pytest.mark.asyncio
async def test_hydrate_mutate_sync_destroy_rehydrate_roundtrip(
    store: MemoryWorkspaceStore,
) -> None:
    scope = "scope-a"
    tree = MemoryTreeIO()

    await store.commit_blob(scope, "readme.md", b"hello", expected_version=0)
    report = await store.hydrate(scope, tree)
    assert "readme.md" in report.written
    assert await tree.read_bytes("readme.md") == b"hello"

    await tree.write_bytes("readme.md", b"hello world")
    await tree.write_bytes("src/main.py", b"print(1)\n")
    sync = await store.sync(scope, tree)
    assert set(sync.written) >= {"readme.md", "src/main.py"}
    assert sync.conflicts == ()

    # Simulate sandbox destroy + new cache.
    tree2 = MemoryTreeIO()
    again = await store.hydrate(scope, tree2)
    assert set(again.written) == {"readme.md", "src/main.py"}
    assert await tree2.read_bytes("readme.md") == b"hello world"
    assert await tree2.read_bytes("src/main.py") == b"print(1)\n"

    rec = await store.stat(scope, "readme.md")
    assert rec is not None
    assert rec.version == 2


@pytest.mark.asyncio
async def test_soft_delete_invisible_to_hydrate(store: MemoryWorkspaceStore) -> None:
    scope = "scope-b"
    await store.commit_blob(scope, "gone.txt", b"x", expected_version=0)
    await store.soft_delete(scope, "gone.txt")
    tree = MemoryTreeIO()
    report = await store.hydrate(scope, tree)
    assert report.written == ()
    assert await tree.list_paths() == []
    assert await store.stat(scope, "gone.txt") is None


@pytest.mark.asyncio
async def test_cas_conflict_fail_closed(store: MemoryWorkspaceStore) -> None:
    scope = "scope-c"
    rec = await store.commit_blob(scope, "a.txt", b"v1", expected_version=0)
    assert rec.version == 1
    with pytest.raises(CasConflict):
        await store.commit_blob(scope, "a.txt", b"v2", expected_version=0)
    # Correct CAS succeeds.
    rec2 = await store.commit_blob(scope, "a.txt", b"v2", expected_version=1)
    assert rec2.version == 2


def test_path_escape_rejected() -> None:
    with pytest.raises(WorkspacePathEscape):
        normalize_workspace_path("../etc/passwd")
    with pytest.raises(WorkspacePathEscape):
        normalize_workspace_path("foo/../../x")


@pytest.mark.asyncio
async def test_r3_object_transient_does_not_bump_version_until_put_ok(
    objects: MemoryObjectStorage,
) -> None:
    store = MemoryWorkspaceStore(objects=objects)
    scope = "scope-d"
    await store.commit_blob(scope, "a.txt", b"ok", expected_version=0)
    objects.fail_puts = 1
    with pytest.raises(ObjectTransient):
        await store.commit_blob(scope, "a.txt", b"next", expected_version=1)
    rec = await store.stat(scope, "a.txt")
    assert rec is not None
    assert rec.version == 1
    assert rec.content_sha256  # still old bytes
    # Retry succeeds.
    rec2 = await store.commit_blob(scope, "a.txt", b"next", expected_version=1)
    assert rec2.version == 2


@pytest.mark.asyncio
async def test_r2_sandbox_kill_rehydrate_from_durable(store: MemoryWorkspaceStore) -> None:
    scope = "scope-e"
    await store.commit_blob(scope, "keep.txt", b"durable", expected_version=0)
    live = MemoryTreeIO()
    await store.hydrate(scope, live)
    await live.write_bytes("keep.txt", b"dirty-not-synced")
    # Kill without sync — new tree must restore durable bytes.
    revived = MemoryTreeIO()
    await store.hydrate(scope, revived)
    assert await revived.read_bytes("keep.txt") == b"durable"


@pytest.mark.asyncio
async def test_sync_soft_deletes_removed_tree_paths(store: MemoryWorkspaceStore) -> None:
    scope = "scope-f"
    await store.commit_blob(scope, "old.txt", b"1", expected_version=0)
    await store.commit_blob(scope, "keep.txt", b"2", expected_version=0)
    tree = MemoryTreeIO()
    await store.hydrate(scope, tree)
    await tree.delete_path("old.txt")
    report = await store.sync(scope, tree)
    assert "old.txt" in report.removed
    assert await store.stat(scope, "old.txt") is None
    assert await store.stat(scope, "keep.txt") is not None


@pytest.mark.asyncio
async def test_sandbox_hydrate_sync_hooks_default_unsupported() -> None:
    from supervisor.sandbox.base import WorkspaceSyncUnsupported
    from supervisor.sandbox.none import NoneProvider

    sb = await NoneProvider().create()
    with pytest.raises(WorkspaceSyncUnsupported):
        await sb.hydrate_workspace("scope")
    with pytest.raises(WorkspaceSyncUnsupported):
        await sb.sync_workspace("scope")
