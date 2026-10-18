"""WP-M3: local_process hydrate/sync via WorkspaceStore (sandbox FS = cache)."""

from __future__ import annotations

from pathlib import Path

import pytest

from supervisor.sandbox.base import WorkspaceSyncUnsupported
from supervisor.sandbox.local_process import LocalProcessProvider
from supervisor.sandbox.none import NoneProvider
from supervisor.seams.providers.memory_object_storage import MemoryObjectStorage
from supervisor.seams.providers.memory_workspace_store import MemoryWorkspaceStore


@pytest.fixture
def store() -> MemoryWorkspaceStore:
    return MemoryWorkspaceStore(objects=MemoryObjectStorage())


@pytest.mark.asyncio
async def test_local_process_hydrate_sync_roundtrip(
    store: MemoryWorkspaceStore, tmp_path: Path
) -> None:
    scope = "scope-m3"
    await store.commit_blob(scope, "readme.md", b"hello", expected_version=0)
    await store.commit_blob(scope, "src/app.py", b"print(1)\n", expected_version=0)

    provider = LocalProcessProvider(base_dir=tmp_path / "sbx", workspace_store=store)
    sb = await provider.create()

    report = await sb.hydrate_workspace(scope)
    assert set(report.written) == {"readme.md", "src/app.py"}
    assert (sb.root / "readme.md").read_bytes() == b"hello"
    assert (sb.root / "src" / "app.py").read_bytes() == b"print(1)\n"

    # Agent mutates cache
    (sb.root / "readme.md").write_bytes(b"hello world")
    (sb.root / "new.txt").write_bytes(b"x")
    (sb.root / "src" / "app.py").unlink()

    sync = await sb.sync_workspace(scope)
    assert "readme.md" in sync.written
    assert "new.txt" in sync.written
    assert "src/app.py" in sync.removed

    # Destroy cache view — new sandbox rehydrates durable SoT
    sb2 = await provider.create()
    again = await sb2.hydrate_workspace(scope)
    assert set(again.written) == {"readme.md", "new.txt"}
    assert (sb2.root / "readme.md").read_bytes() == b"hello world"
    assert not (sb2.root / "src" / "app.py").exists()


@pytest.mark.asyncio
async def test_local_process_without_store_raises(tmp_path: Path) -> None:
    provider = LocalProcessProvider(base_dir=tmp_path / "sbx")
    sb = await provider.create()
    with pytest.raises(WorkspaceSyncUnsupported):
        await sb.hydrate_workspace("s")
    with pytest.raises(WorkspaceSyncUnsupported):
        await sb.sync_workspace("s")


@pytest.mark.asyncio
async def test_none_sandbox_still_unsupported() -> None:
    sb = await NoneProvider().create()
    with pytest.raises(WorkspaceSyncUnsupported):
        await sb.hydrate_workspace("s")


@pytest.mark.asyncio
async def test_r2_sandbox_kill_rehydrate_from_durable(
    store: MemoryWorkspaceStore, tmp_path: Path
) -> None:
    scope = "scope-r2"
    await store.commit_blob(scope, "keep.txt", b"durable", expected_version=0)
    provider = LocalProcessProvider(base_dir=tmp_path / "sbx", workspace_store=store)
    live = await provider.create()
    await live.hydrate_workspace(scope)
    (live.root / "keep.txt").write_bytes(b"dirty-not-synced")
    # Kill without sync
    revived = await provider.create()
    await revived.hydrate_workspace(scope)
    assert (revived.root / "keep.txt").read_bytes() == b"durable"
