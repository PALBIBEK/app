"""Object storage in a directory: one store several worker processes share (chaos tests)."""

from __future__ import annotations

import hashlib
from collections.abc import Iterator, MutableMapping
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from supervisor.seams.providers.storage.memory_object_storage import MemoryObjectStorage
from supervisor.seams.providers.workspace.postgres_workspace_store import PostgresWorkspaceStore


class _DirObjects(MutableMapping[str, bytes]):
    def __init__(self, root: Path) -> None:
        self._root = root
        root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        return self._root / hashlib.sha256(key.encode()).hexdigest()

    def __getitem__(self, key: str) -> bytes:
        try:
            return self._path(key).read_bytes()
        except FileNotFoundError:
            raise KeyError(key) from None

    def __setitem__(self, key: str, value: bytes) -> None:
        tmp = self._path(key).with_suffix(".tmp")
        tmp.write_bytes(value)
        tmp.replace(self._path(key))

    def __delitem__(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)

    def __iter__(self) -> Iterator[str]:  # keys are hashed: listing is not needed here
        return iter(())

    def __len__(self) -> int:
        return 0

    def __contains__(self, key: object) -> bool:
        return isinstance(key, str) and self._path(key).exists()


def shared_workspace_store(factory: async_sessionmaker[AsyncSession], root: Path) -> PostgresWorkspaceStore:
    objects: Any = MemoryObjectStorage(_objects=_DirObjects(root))  # type: ignore[arg-type]
    return PostgresWorkspaceStore(factory, objects)
