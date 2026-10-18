"""WP-M6 chaos helpers — fault injection for recovery-matrix tests."""

from __future__ import annotations

from dataclasses import dataclass, field

from supervisor.seams.providers.memory_workspace_store import (
    MemoryTreeIO,
    normalize_workspace_path,
)
from supervisor.seams.workspace_store import WorkspaceTreeIO


@dataclass
class CrashyTreeIO:
    """TreeIO that fails after N successful writes (R8 partial hydrate)."""

    inner: MemoryTreeIO = field(default_factory=MemoryTreeIO)
    fail_after_n_writes: int = 0
    _writes: int = 0

    async def write_bytes(self, path: str, data: bytes) -> None:
        if (
            self.fail_after_n_writes > 0
            and self._writes >= self.fail_after_n_writes
        ):
            raise RuntimeError("injected mid-hydrate crash")
        self._writes += 1
        await self.inner.write_bytes(path, data)

    async def read_bytes(self, path: str) -> bytes:
        return await self.inner.read_bytes(path)

    async def list_paths(self) -> list[str]:
        return await self.inner.list_paths()

    async def delete_path(self, path: str) -> None:
        await self.inner.delete_path(path)

    async def clear(self) -> None:
        await self.inner.clear()
        self._writes = 0


def as_crashy_tree(tree: CrashyTreeIO) -> WorkspaceTreeIO:
    return tree


async def bump_durable_version(store: object, scope_id: str, path: str, data: bytes) -> None:
    """Simulate concurrent durable writer (R10 mid-sync CAS)."""
    path = normalize_workspace_path(path)
    rec = await store.stat(scope_id, path)  # type: ignore[attr-defined]
    expected = 0 if rec is None else rec.version
    await store.commit_blob(scope_id, path, data, expected_version=expected)  # type: ignore[attr-defined]
