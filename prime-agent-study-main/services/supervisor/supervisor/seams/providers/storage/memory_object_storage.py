"""In-memory ObjectStorage for contract tests (no network / Docker)."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

from supervisor.seams.services.object_storage import (
    HeadResult,
    ObjectIntegrityError,
    ObjectNotFound,
    ObjectTransient,
    PresignedUrl,
    PutResult,
)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@dataclass
class MemoryObjectStorage:
    """Fake S3: dict of key → bytes. Structurally implements ObjectStorage."""

    _objects: dict[str, bytes] = field(default_factory=dict)
    fail_puts: int = 0  # inject ObjectTransient for R3 tests

    async def put(
        self,
        key: str,
        data: bytes,
        *,
        content_type: str | None = None,
        content_sha256: str | None = None,
    ) -> PutResult:
        del content_type
        if self.fail_puts > 0:
            self.fail_puts -= 1
            raise ObjectTransient("injected put failure")
        digest = _sha256(data)
        if content_sha256 is not None and content_sha256 != digest:
            raise ObjectIntegrityError(
                f"sha mismatch for {key}: expected {content_sha256} got {digest}"
            )
        self._objects[key] = data
        return PutResult(key=key, content_sha256=digest, size_bytes=len(data))

    async def get(self, key: str) -> bytes:
        try:
            return self._objects[key]
        except KeyError as exc:
            raise ObjectNotFound(key) from exc

    async def head(self, key: str) -> HeadResult:
        data = self._objects.get(key)
        if data is None:
            return HeadResult(key=key, size_bytes=0, content_sha256=None, exists=False)
        return HeadResult(
            key=key,
            size_bytes=len(data),
            content_sha256=_sha256(data),
            exists=True,
        )

    async def delete(self, key: str) -> None:
        self._objects.pop(key, None)

    async def exists(self, key: str) -> bool:
        return key in self._objects

    async def create_download_url(self, key: str, *, ttl_s: int) -> PresignedUrl:
        if key not in self._objects:
            raise ObjectNotFound(key)
        return PresignedUrl(
            url=f"memory://download/{key}?ttl={ttl_s}",
            method="GET",
            expires_in_s=ttl_s,
        )



