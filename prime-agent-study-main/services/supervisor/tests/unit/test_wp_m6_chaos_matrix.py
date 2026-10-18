"""WP-M6 chaos matrix — recovery cases R2/R3/R6–R12 (+ R1-light, R8 ready)."""

from __future__ import annotations

from pathlib import Path

import pytest

from supervisor.sandbox.local_process import LocalProcessProvider
from supervisor.sandbox.workspace_tree import LocalFsTreeIO
from supervisor.seams.object_storage import ObjectTransient
from supervisor.seams.providers.memory_object_storage import MemoryObjectStorage
from supervisor.seams.providers.memory_workspace_store import (
    MemoryTreeIO,
    MemoryWorkspaceStore,
)
from supervisor.seams.session_workspace import (
    SessionWorkspaceService,
    SessionWorkspaceState,
    WorkspaceConflictError,
    WorkspaceNotReadyError,
)
from supervisor.seams.authz import Grant, Principal
from supervisor.seams.providers.grant_store_factory import (
    create_authz_service,
    create_grant_store,
)
from supervisor.seams.workspace_hydrator import StoreWorkspaceHydrator
from supervisor.seams.workspace_store import (
    CasConflict,
    ChecksumMismatch,
    HydrateIncomplete,
    WorkspacePathEscape,
)
from tests.support.workspace_chaos import CrashyTreeIO, bump_durable_version


@pytest.fixture
def objects() -> MemoryObjectStorage:
    return MemoryObjectStorage()


@pytest.fixture
def store(objects: MemoryObjectStorage) -> MemoryWorkspaceStore:
    return MemoryWorkspaceStore(objects=objects)


@pytest.mark.asyncio
async def test_r2_sandbox_gone_rehydrate_matches_sha(
    store: MemoryWorkspaceStore, tmp_path: Path
) -> None:
    scope = "r2"
    await store.commit_blob(scope, "keep.txt", b"durable-v1", expected_version=0)
    provider = LocalProcessProvider(base_dir=tmp_path / "sbx", workspace_store=store)
    live = await provider.create()
    await live.hydrate_workspace(scope)
    (live.root / "keep.txt").write_bytes(b"dirty")
    # Sandbox "gone" — new create + hydrate
    revived = await provider.create()
    await revived.hydrate_workspace(scope)
    assert (revived.root / "keep.txt").read_bytes() == b"durable-v1"
    rec = await store.stat(scope, "keep.txt")
    assert rec is not None
    assert rec.content_sha256


@pytest.mark.asyncio
async def test_r3_object_transient_on_sync_does_not_bump_version(
    objects: MemoryObjectStorage, store: MemoryWorkspaceStore
) -> None:
    scope = "r3"
    await store.commit_blob(scope, "a.txt", b"ok", expected_version=0)
    tree = MemoryTreeIO()
    await store.hydrate(scope, tree)
    await tree.write_bytes("a.txt", b"next")
    objects.fail_puts = 1
    with pytest.raises(ObjectTransient):
        await store.sync(scope, tree)
    rec = await store.stat(scope, "a.txt")
    assert rec is not None
    assert rec.version == 1
    assert await objects.get(rec.object_key) == b"ok"


@pytest.mark.asyncio
async def test_r3_retry_after_transient_succeeds(
    objects: MemoryObjectStorage, store: MemoryWorkspaceStore
) -> None:
    scope = "r3b"
    await store.commit_blob(scope, "a.txt", b"ok", expected_version=0)
    tree = MemoryTreeIO()
    await store.hydrate(scope, tree)
    await tree.write_bytes("a.txt", b"next")
    objects.fail_puts = 1
    with pytest.raises(ObjectTransient):
        await store.sync(scope, tree)
    report = await store.sync(scope, tree)
    assert "a.txt" in report.written
    rec = await store.stat(scope, "a.txt")
    assert rec is not None and rec.version == 2
    assert await objects.get(rec.object_key) == b"next"


@pytest.mark.asyncio
async def test_r6_cas_conflict_fail_closed(store: MemoryWorkspaceStore) -> None:
    scope = "r6"
    rec = await store.commit_blob(scope, "a.txt", b"v1", expected_version=0)
    assert rec.version == 1
    with pytest.raises(CasConflict):
        await store.commit_blob(scope, "a.txt", b"stale", expected_version=0)
    still = await store.stat(scope, "a.txt")
    assert still is not None and still.version == 1


@pytest.mark.asyncio
async def test_r7_kill_without_sync_discards_dirty(
    store: MemoryWorkspaceStore, tmp_path: Path
) -> None:
    scope = "r7"
    await store.commit_blob(scope, "keep.txt", b"durable", expected_version=0)
    provider = LocalProcessProvider(base_dir=tmp_path / "sbx", workspace_store=store)
    live = await provider.create()
    await live.hydrate_workspace(scope)
    (live.root / "keep.txt").write_bytes(b"dirty-not-synced")
    revived = await provider.create()
    await revived.hydrate_workspace(scope)
    assert (revived.root / "keep.txt").read_bytes() == b"durable"


@pytest.mark.asyncio
async def test_r8_partial_hydrate_raises_incomplete(
    store: MemoryWorkspaceStore,
) -> None:
    scope = "r8"
    await store.commit_blob(scope, "a.txt", b"1", expected_version=0)
    await store.commit_blob(scope, "b.txt", b"2", expected_version=0)
    crashy = CrashyTreeIO(fail_after_n_writes=1)
    with pytest.raises(HydrateIncomplete):
        await store.hydrate(scope, crashy)


@pytest.mark.asyncio
async def test_r8_retry_after_incomplete_full_clear_hydrate(
    store: MemoryWorkspaceStore,
) -> None:
    scope = "r8b"
    await store.commit_blob(scope, "a.txt", b"1", expected_version=0)
    await store.commit_blob(scope, "b.txt", b"2", expected_version=0)
    crashy = CrashyTreeIO(fail_after_n_writes=1)
    with pytest.raises(HydrateIncomplete):
        await store.hydrate(scope, crashy)
    # Retry with healthy tree — clear then full fill
    tree = MemoryTreeIO()
    report = await store.hydrate(scope, tree)
    assert set(report.written) == {"a.txt", "b.txt"}
    assert await tree.read_bytes("a.txt") == b"1"


@pytest.mark.asyncio
async def test_r8_tools_refused_while_not_ready() -> None:
    state = SessionWorkspaceState(
        scope_id="s",
        principal=Principal(id="p", tenant_id="t"),
        ready=False,
    )
    svc = SessionWorkspaceService()
    with pytest.raises(WorkspaceNotReadyError):
        svc.assert_ready(state)


@pytest.mark.asyncio
async def test_r9_hydrate_prunes_stale_empty_dirs(
    store: MemoryWorkspaceStore, tmp_path: Path
) -> None:
    scope = "r9"
    await store.commit_blob(scope, "only.txt", b"x", expected_version=0)
    provider = LocalProcessProvider(base_dir=tmp_path / "sbx", workspace_store=store)
    sb = await provider.create()
    stale = sb.root / "gone" / "nested"
    stale.mkdir(parents=True)
    (stale / "old.txt").write_bytes(b"stale")
    await sb.hydrate_workspace(scope)
    assert (sb.root / "only.txt").read_bytes() == b"x"
    assert not (sb.root / "gone").exists()


@pytest.mark.asyncio
async def test_r10_cas_conflict_during_sync_lists_conflicts(
    objects: MemoryObjectStorage,
) -> None:
    store = MemoryWorkspaceStore(objects=objects)

    async def _bump(scope_id: str, path: str) -> None:
        await bump_durable_version(store, scope_id, path, b"from-other-writer")

    store.before_sync_cas = _bump
    scope = "r10"
    await store.commit_blob(scope, "a.txt", b"v1", expected_version=0)
    tree = MemoryTreeIO()
    await store.hydrate(scope, tree)
    await tree.write_bytes("a.txt", b"from-sandbox")
    report = await store.sync(scope, tree)
    assert "a.txt" in report.conflicts
    rec = await store.stat(scope, "a.txt")
    assert rec is not None
    assert await store.objects.get(rec.object_key) == b"from-other-writer"


@pytest.mark.asyncio
async def test_r10_sync_if_dirty_raises_workspace_conflict_error(
    objects: MemoryObjectStorage, tmp_path: Path
) -> None:
    store = MemoryWorkspaceStore(objects=objects)

    async def _bump(scope_id: str, path: str) -> None:
        await bump_durable_version(store, scope_id, path, b"other")

    store.before_sync_cas = _bump
    scope = "r10b"
    grant_store = create_grant_store("memory")
    await grant_store.bootstrap_owner("p1", scope)
    authz = create_authz_service(store=grant_store)
    hydrator = StoreWorkspaceHydrator(store)
    svc = SessionWorkspaceService(authz=authz, hydrator=hydrator)
    await store.commit_blob(scope, "a.txt", b"v1", expected_version=0)
    provider = LocalProcessProvider(base_dir=tmp_path / "sbx", hydrator=hydrator)
    sb = await provider.create()
    state = SessionWorkspaceState(
        scope_id=scope,
        principal=Principal(id="p1", tenant_id="t1"),
    )
    await svc.ensure_hydrated(state, sb)
    (sb.root / "a.txt").write_bytes(b"sandbox")
    svc.mark_dirty(state)
    with pytest.raises(WorkspaceConflictError) as ei:
        await svc.sync_if_dirty(state, sb)
    assert "a.txt" in ei.value.conflicts


@pytest.mark.asyncio
async def test_r11_checksum_mismatch_does_not_write_cache(
    store: MemoryWorkspaceStore, tmp_path: Path
) -> None:
    scope = "r11"
    rec = await store.commit_blob(scope, "bad.txt", b"good", expected_version=0)
    await store.objects.put(rec.object_key, b"tampered")
    tree_root = tmp_path / "tree"
    tree_root.mkdir()
    tree = LocalFsTreeIO(tree_root)
    with pytest.raises(ChecksumMismatch):
        await store.hydrate(scope, tree)
    assert list(tree_root.rglob("*")) == [] or not (tree_root / "bad.txt").exists()


@pytest.mark.asyncio
async def test_r12_dotdot_and_symlink_escape_rejected(tmp_path: Path) -> None:
    root = tmp_path / "ws"
    root.mkdir()
    outside = tmp_path / "secret.txt"
    outside.write_text("nope", encoding="utf-8")
    tree = LocalFsTreeIO(root)
    with pytest.raises(WorkspacePathEscape):
        await tree.write_bytes("../secret.txt", b"x")
    # Symlink inside workspace pointing outside
    link = root / "leak"
    link.symlink_to(outside)
    with pytest.raises(WorkspacePathEscape):
        await tree.read_bytes("leak")


@pytest.mark.asyncio
async def test_r1_light_unpaired_toolstart_no_version_bump_without_put(
    objects: MemoryObjectStorage, store: MemoryWorkspaceStore
) -> None:
    """R1-light: tool intent without successful put must not bump durable version."""
    scope = "r1"
    await store.commit_blob(scope, "a.txt", b"base", expected_version=0)
    before = await store.stat(scope, "a.txt")
    assert before is not None
    objects.fail_puts = 1
    tree = MemoryTreeIO()
    await store.hydrate(scope, tree)
    await tree.write_bytes("a.txt", b"attempt")
    with pytest.raises(ObjectTransient):
        await store.sync(scope, tree)
    after = await store.stat(scope, "a.txt")
    assert after is not None
    assert after.version == before.version
    assert await objects.get(after.object_key) == b"base"
