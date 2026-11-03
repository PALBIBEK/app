"""Where the journal keeps what is too large for a row (a long conversation, a big tool
result) and a lost sandbox's logs: content-addressed bytes, named by their SHA-256.

Every API replica and every worker reads every session (a model step runs on any worker), so
in a deployment of more than one process the bytes must be where all of them see them: the
object storage the platform already uses (``ObjectBlobs``). A directory (``LocalBlobs``) is
for one process, or for processes that share it (dev, tests). A blob written on one machine
and read on another was the journal's first multi-replica failure: the whole session became
unreadable on every other machine.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import re
from pathlib import Path
from typing import Final, Protocol

from supervisor.seams.services.object_storage import ObjectNotFound, ObjectStorage

__all__ = ["BLOB_PREFIX", "BlobStore", "LocalBlobs", "ObjectBlobs", "blob_ref", "valid_ref"]

BLOB_PREFIX: Final = "journal/blobs/"
"""Where ``ObjectBlobs`` keeps them in the platform's bucket."""

_REF: Final = re.compile(r"[0-9a-f]{64}")


def blob_ref(data: bytes) -> str:
    """The name ``data`` is kept under (its SHA-256): the same bytes are kept once."""
    return hashlib.sha256(data).hexdigest()


def valid_ref(ref: str) -> bool:
    return bool(_REF.fullmatch(ref))


class BlobStore(Protocol):
    async def put(self, ref: str, data: bytes) -> None:
        """Keep ``data`` under ``ref`` (its ``blob_ref``); a no-op if it is kept already."""
        ...

    async def get(self, ref: str) -> bytes:
        """The bytes under ``ref``; ``FileNotFoundError`` when there are none."""
        ...

    async def delete(self, ref: str) -> bool:
        """Forget ``ref``; whether anything was kept under it."""
        ...


class LocalBlobs:
    """A directory: for one process, or processes that share the directory."""

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, ref: str) -> Path:
        if not valid_ref(ref):
            raise FileNotFoundError(ref)
        return self.root / ref

    async def put(self, ref: str, data: bytes) -> None:
        path = self._path(ref)
        if not path.exists():
            await asyncio.to_thread(_write_atomically, path, data)

    async def get(self, ref: str) -> bytes:
        return await asyncio.to_thread(self._path(ref).read_bytes)

    async def delete(self, ref: str) -> bool:
        try:
            await asyncio.to_thread(self._path(ref).unlink)
        except FileNotFoundError:
            return False
        return True


class ObjectBlobs:
    """The platform's object storage, which every replica and worker shares."""

    def __init__(self, objects: ObjectStorage, *, prefix: str = BLOB_PREFIX) -> None:
        self.objects = objects
        self._prefix = prefix

    def _key(self, ref: str) -> str:
        if not valid_ref(ref):
            raise FileNotFoundError(ref)
        return f"{self._prefix}{ref}"

    async def put(self, ref: str, data: bytes) -> None:
        key = self._key(ref)
        if not await self.objects.exists(key):
            await self.objects.put(key, data, content_type="application/octet-stream", content_sha256=ref)

    async def get(self, ref: str) -> bytes:
        try:
            return await self.objects.get(self._key(ref))
        except ObjectNotFound as exc:
            raise FileNotFoundError(ref) from exc

    async def delete(self, ref: str) -> bool:
        key = self._key(ref)
        if not await self.objects.exists(key):
            return False
        with contextlib.suppress(ObjectNotFound):
            await self.objects.delete(key)
        return True


def _write_atomically(path: Path, data: bytes) -> None:
    """Write ``data`` to ``path`` so no reader ever sees part of it (two writers of the same
    content-addressed blob race harmlessly: both write the same bytes)."""
    temp = path.with_suffix(f".{hashlib.sha256(str(id(data)).encode()).hexdigest()[:8]}.tmp")
    temp.write_bytes(data)
    temp.replace(path)
