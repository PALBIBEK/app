"""WP-M7 path confinement — TreeIO + LocalProcessSandbox read/write/exists."""

from __future__ import annotations

from pathlib import Path

import pytest

from supervisor.sandbox.local_process import LocalProcessProvider
from supervisor.sandbox.workspace_tree import LocalFsTreeIO
from supervisor.seams.workspace_store import WorkspacePathEscape


@pytest.mark.asyncio
async def test_treeio_rejects_dotdot_abs_nul(tmp_path: Path) -> None:
    tree = LocalFsTreeIO(tmp_path)
    with pytest.raises(WorkspacePathEscape):
        await tree.write_bytes("../escape.txt", b"x")
    with pytest.raises(WorkspacePathEscape):
        await tree.write_bytes("/etc/passwd", b"x")
    # NUL rejected at workspace_relpath layer for tools; TreeIO _safe_rel
    # treats path as relative segments — empty after strip raises.
    with pytest.raises(WorkspacePathEscape):
        await tree.write_bytes("", b"x")


@pytest.mark.asyncio
async def test_treeio_symlink_escape_rejected(tmp_path: Path) -> None:
    root = tmp_path / "ws"
    root.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_bytes(b"secret")
    link = root / "leak"
    link.symlink_to(outside)
    tree = LocalFsTreeIO(root)
    with pytest.raises(WorkspacePathEscape):
        await tree.read_bytes("leak")


@pytest.mark.asyncio
async def test_local_process_read_write_rejects_escape(tmp_path: Path) -> None:
    provider = LocalProcessProvider(base_dir=tmp_path / "sbx")
    sb = await provider.create()
    await sb.write_file("ok.txt", b"in")
    assert await sb.read_file("ok.txt") == b"in"
    assert await sb.exists("ok.txt") is True

    with pytest.raises(WorkspacePathEscape):
        await sb.write_file("../escape.txt", b"x")
    with pytest.raises(WorkspacePathEscape):
        await sb.read_file("../../etc/passwd")
    with pytest.raises(WorkspacePathEscape):
        await sb.exists("../escape.txt")

    # Logical /workspace prefix still confined
    await sb.write_file("/workspace/nested.txt", b"y")
    assert await sb.read_file("/workspace/nested.txt") == b"y"


@pytest.mark.asyncio
async def test_list_paths_skips_symlink_out(tmp_path: Path) -> None:
    root = tmp_path / "ws"
    root.mkdir()
    (root / "keep.txt").write_bytes(b"k")
    outside = tmp_path / "out.txt"
    outside.write_bytes(b"secret")
    (root / "leak").symlink_to(outside)
    tree = LocalFsTreeIO(root)
    paths = await tree.list_paths()
    assert "keep.txt" in paths
    assert "leak" not in paths
