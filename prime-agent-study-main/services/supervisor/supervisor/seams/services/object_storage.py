"""ObjectStorage seam — durable blob bytes (S3/Seaweed/GCS behind Protocol).

Patterns: Protocol + frozen DTOs (PYTHON_PATTERNS_MASTERCLASS §2/§5/§7).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from supervisor.seams.core.errors import SeamError

__all__ = [
    "DirectUploadUnsupported",
    "HeadResult",
    "ObjectIntegrityError",
    "ObjectNotFound",
    "ObjectStorage",
    "ObjectStorageError",
    "ObjectTransient",
    "PresignedUpload",
    "PresignedUrl",
    "PutResult",
    "StoredDigest",
]


class ObjectStorageError(SeamError):
    def __init__(self, message: str, code: str) -> None:
        super().__init__(message, code)


class ObjectNotFound(ObjectStorageError):
    def __init__(self, key: str) -> None:
        super().__init__(f"object not found: {key}", "OBJECT_NOT_FOUND")
        self.key = key


class ObjectTransient(ObjectStorageError):
    def __init__(self, message: str) -> None:
        super().__init__(message, "OBJECT_TRANSIENT")


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


@dataclass(frozen=True)
class PresignedUpload:
    """Where a client sends a file's bytes itself, and the headers it must send with them
    (they are signed: storage that checks signatures refuses other bytes or lengths)."""

    url: str
    method: str
    headers: dict[str, str]
    expires_in_s: int


@dataclass(frozen=True)
class StoredDigest:
    """What an object really holds, measured by the server (never taken from the client)."""

    content_sha256: str
    size_bytes: int


class DirectUploadUnsupported(ObjectStorageError):
    """This storage cannot hand out upload URLs (e.g. in-memory); send the bytes through the API."""

    def __init__(self) -> None:
        super().__init__("this object storage does not support direct uploads", "OBJECT_NO_DIRECT_UPLOAD")


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
        self, key: str, *, size_bytes: int, content_sha256: str, content_type: str, ttl_s: int
    ) -> PresignedUpload:
        """A URL the client PUTs exactly these bytes to. Raises DirectUploadUnsupported."""
        ...

    async def digest(self, key: str, *, max_bytes: int) -> StoredDigest:
        """SHA-256 and size of what ``key`` holds (streamed; refuses past ``max_bytes``)."""
        ...

    async def copy(self, src: str, dst: str, *, content_sha256: str) -> None:
        """Copy inside the store (bytes never pass through the caller)."""
        ...

