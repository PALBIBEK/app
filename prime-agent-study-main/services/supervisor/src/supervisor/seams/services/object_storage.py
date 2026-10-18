"""ObjectStorage seam — durable blob bytes (S3/Seaweed/GCS behind Protocol).

Patterns: Protocol + frozen DTOs (PYTHON_PATTERNS_MASTERCLASS §2/§5/§7).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from supervisor.seams.core.errors import SeamError

__all__ = [
    "HeadResult",
    "ObjectConflict",
    "ObjectForbidden",
    "ObjectIntegrityError",
    "ObjectNotFound",
    "ObjectStorage",
    "ObjectStorageError",
    "ObjectTransient",
    "PresignedUrl",
    "PutResult",
]


class ObjectStorageError(SeamError):
    def __init__(self, message: str, code: str) -> None:
        super().__init__(message, code)


class ObjectNotFound(ObjectStorageError):
    def __init__(self, key: str) -> None:
        super().__init__(f"object not found: {key}", "OBJECT_NOT_FOUND")
        self.key = key


class ObjectConflict(ObjectStorageError):
    def __init__(self, message: str) -> None:
        super().__init__(message, "OBJECT_CONFLICT")


class ObjectTransient(ObjectStorageError):
    def __init__(self, message: str) -> None:
        super().__init__(message, "OBJECT_TRANSIENT")


class ObjectForbidden(ObjectStorageError):
    def __init__(self, message: str) -> None:
        super().__init__(message, "OBJECT_FORBIDDEN")


class ObjectIntegrityError(ObjectStorageError):
    def __init__(self, message: str) -> None:
        super().__init__(message, "OBJECT_INTEGRITY")


@dataclass(frozen=True)
class PutResult:
    key: str
    content_sha256: str
    size_bytes: int


@dataclass(frozen=True)
class HeadResult:
    key: str
    size_bytes: int
    content_sha256: str | None
    exists: bool = True


@dataclass(frozen=True)
class PresignedUrl:
    url: str
    method: str
    expires_in_s: int


@runtime_checkable
class ObjectStorage(Protocol):
    """Async-only blob store. Sync SDKs wrap with ``asyncio.to_thread`` in adapters."""

    async def put(
        self,
        key: str,
        data: bytes,
        *,
        content_type: str | None = None,
        content_sha256: str | None = None,
    ) -> PutResult: ...

    async def get(self, key: str) -> bytes: ...

    async def head(self, key: str) -> HeadResult: ...

    async def delete(self, key: str) -> None: ...

    async def exists(self, key: str) -> bool: ...

    async def create_download_url(self, key: str, *, ttl_s: int) -> PresignedUrl: ...

    async def create_upload_url(
        self,
        key: str,
        *,
        ttl_s: int,
        content_type: str | None = None,
    ) -> PresignedUrl: ...
