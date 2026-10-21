"""Sandbox filesystem as WorkspaceTreeIO (hydrate/sync target — cache only)."""

from __future__ import annotations

import asyncio
import os
import shutil
from pathlib import Path

from supervisor.seams.services.workspace_store import WorkspacePathEscape


def _safe_rel(path: str) -> str:
    raw = path.strip().replace("\\", "/")
    # Absolute paths are not accepted — callers strip logical /workspace first.
    if raw.startswith("/"):
        raise WorkspacePathEscape(path)
    parts: list[str] = []
    for part in raw.split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            raise WorkspacePathEscape(path)
        parts.append(part)
    if not parts:
        raise WorkspacePathEscape(path)
    return "/".join(parts)


def _resolved_under(root: Path, rel: str) -> Path:
    """Join + resolve; reject escapes past root including via symlinks (R12)."""
    root_real = root.resolve()
    dest = (root_real / rel).resolve()
    try:
        dest.relative_to(root_real)
    except ValueError as exc:
        raise WorkspacePathEscape(rel) from exc
    return dest


class LocalFsTreeIO:
    """Walk a host directory tree as the sandbox ``/workspace`` cache."""

    def __init__(self, root: Path) -> None:
        self._root = root.resolve()

    async def write_bytes(self, path: str, data: bytes) -> None:
        await asyncio.to_thread(self._write_bytes_sync, path, data)

    def _write_bytes_sync(self, path: str, data: bytes) -> None:
        rel = _safe_rel(path)
        dest = _resolved_under(self._root, rel)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)

    async def read_bytes(self, path: str) -> bytes:
        return await asyncio.to_thread(self._read_bytes_sync, path)

    def _read_bytes_sync(self, path: str) -> bytes:
        rel = _safe_rel(path)
        src = _resolved_under(self._root, rel)
        return src.read_bytes()

    async def list_paths(self) -> list[str]:
        return await asyncio.to_thread(self._list_paths_sync)

    def _list_paths_sync(self) -> list[str]:
        if not self._root.exists():
            return []
        out: list[str] = []
        # followlinks=False — do not walk through directory symlinks (WP-M7).
        for dirpath, _dirnames, filenames in os.walk(
            self._root, followlinks=False
        ):
            base = Path(dirpath)
            for name in filenames:
                p = base / name
                if p.is_symlink():
                    try:
                        _resolved_under(self._root, p.relative_to(self._root).as_posix())
                    except (WorkspacePathEscape, ValueError):
                        continue
                if p.is_file():
                    out.append(p.relative_to(self._root).as_posix())
        return sorted(out)

    async def delete_path(self, path: str) -> None:
        await asyncio.to_thread(self._delete_path_sync, path)

    def _delete_path_sync(self, path: str) -> None:
        rel = _safe_rel(path)
        target = _resolved_under(self._root, rel)
        if target.is_file():
            target.unlink()
        elif target.is_dir():
            shutil.rmtree(target)

    async def clear(self) -> None:
        await asyncio.to_thread(self._clear_sync)

    def _clear_sync(self) -> None:
        if not self._root.exists():
            self._root.mkdir(parents=True, exist_ok=True)
            return
        for child in self._root.iterdir():
            if child.is_dir():
                shutil.rmtree(child)
            else:
                child.unlink()
