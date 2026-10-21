"""S3-compatible ObjectStorage adapter (SeaweedFS / MinIO / GCS via S3 API).

Injectable sync client port + ``asyncio.to_thread`` — unit tests need no network
(PYTHON_PATTERNS_MASTERCLASS: Protocol + Factory + to_thread).
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
from dataclasses import dataclass
from typing import Any, Protocol

from supervisor.seams.services.object_storage import (
    HeadResult,
    ObjectIntegrityError,
    ObjectNotFound,
    ObjectStorageError,
    ObjectTransient,
    PresignedUpload,
    PresignedUrl,
    PutResult,
    StoredDigest,
)

_CHUNK = 1024 * 1024


class S3ClientPort(Protocol):
    def put_object(self, *, Bucket: str, Key: str, Body: bytes, **kwargs: Any) -> Any: ...

    def get_object(self, *, Bucket: str, Key: str) -> Any: ...

    def head_object(self, *, Bucket: str, Key: str, **kwargs: Any) -> Any: ...

    def copy_object(self, *, Bucket: str, Key: str, CopySource: dict[str, str], **kwargs: Any) -> Any: ...

    def delete_object(self, *, Bucket: str, Key: str) -> Any: ...

    def generate_presigned_url(
        self,
        ClientMethod: str,
        Params: dict[str, Any],
        ExpiresIn: int,
    ) -> str: ...


@dataclass
class S3ObjectStorage:
    """ObjectStorage over any S3-compatible endpoint."""

    client: S3ClientPort
    bucket: str
    presign_client: S3ClientPort | None = None
    """Signs URLs that browsers use: the store's public address (OBJECT_STORAGE_PUBLIC_ENDPOINT)
    when the supervisor reaches it by another (e.g. a compose service name)."""

    async def put(
        self,
        key: str,
        data: bytes,
        *,
        content_type: str | None = None,
        content_sha256: str | None = None,
    ) -> PutResult:
        digest = hashlib.sha256(data).hexdigest()
        if content_sha256 is not None and content_sha256 != digest:
            raise ObjectIntegrityError(
                f"sha mismatch for {key}: expected {content_sha256} got {digest}"
            )
        kwargs: dict[str, Any] = {
            "Bucket": self.bucket,
            "Key": key,
            "Body": data,
            "Metadata": {"sha256": digest},
        }
        if content_type:
            kwargs["ContentType"] = content_type
        try:
            await asyncio.to_thread(self.client.put_object, **kwargs)
        except Exception as exc:
            raise ObjectTransient(str(exc)) from exc
        return PutResult(key=key, content_sha256=digest, size_bytes=len(data))

    async def get(self, key: str) -> bytes:
        try:
            resp = await asyncio.to_thread(
                self.client.get_object, Bucket=self.bucket, Key=key
            )
            body = resp["Body"]
            return await asyncio.to_thread(body.read)
        except Exception as exc:
            if _is_not_found(exc):
                raise ObjectNotFound(key) from exc
            raise ObjectTransient(str(exc)) from exc

    async def head(self, key: str) -> HeadResult:
        try:
            resp = await asyncio.to_thread(
                self.client.head_object, Bucket=self.bucket, Key=key
            )
        except Exception as exc:
            if _is_not_found(exc):
                return HeadResult(
                    key=key, size_bytes=0, content_sha256=None, exists=False
                )
            raise ObjectTransient(str(exc)) from exc
        meta = resp.get("Metadata") or {}
        return HeadResult(
            key=key,
            size_bytes=int(resp.get("ContentLength") or 0),
            content_sha256=meta.get("sha256"),
            exists=True,
        )

    async def delete(self, key: str) -> None:
        try:
            await asyncio.to_thread(
                self.client.delete_object, Bucket=self.bucket, Key=key
            )
        except Exception as exc:
            if _is_not_found(exc):
                return
            raise ObjectTransient(str(exc)) from exc

    async def exists(self, key: str) -> bool:
        return (await self.head(key)).exists

    async def create_download_url(self, key: str, *, ttl_s: int) -> PresignedUrl:
        if not await self.exists(key):
            raise ObjectNotFound(key)
        try:
            url = await asyncio.to_thread(
                self.client.generate_presigned_url,
                "get_object",
                {"Bucket": self.bucket, "Key": key},
                ttl_s,
            )
        except Exception as exc:
            raise ObjectStorageError(str(exc), "OBJECT_PRESIGN") from exc
        return PresignedUrl(url=url, method="GET", expires_in_s=ttl_s)

    async def create_upload_url(
        self, key: str, *, size_bytes: int, content_sha256: str, content_type: str, ttl_s: int
    ) -> PresignedUpload:
        checksum = base64.b64encode(bytes.fromhex(content_sha256)).decode()
        try:
            url = await asyncio.to_thread(
                (self.presign_client or self.client).generate_presigned_url,
                "put_object",
                {
                    "Bucket": self.bucket,
                    "Key": key,
                    # Signed: S3 refuses another length, type or checksum (BadDigest).
                    "ContentLength": size_bytes,
                    "ContentType": content_type,
                    "ChecksumSHA256": checksum,
                },
                ttl_s,
            )
        except Exception as exc:
            raise ObjectStorageError(str(exc), "OBJECT_PRESIGN") from exc
        return PresignedUpload(
            url=url,
            method="PUT",
            headers={"Content-Type": content_type, "x-amz-checksum-sha256": checksum},
            expires_in_s=ttl_s,
        )

    async def digest(self, key: str, *, max_bytes: int) -> StoredDigest:
        try:
            head = await asyncio.to_thread(
                self.client.head_object, Bucket=self.bucket, Key=key, ChecksumMode="ENABLED"
            )
        except Exception as exc:
            if _is_not_found(exc):
                raise ObjectNotFound(key) from exc
            raise ObjectTransient(str(exc)) from exc
        size = int(head.get("ContentLength") or 0)
        if size > max_bytes:
            raise ObjectIntegrityError(f"{key} holds {size} bytes, more than the {max_bytes} allowed")
        verified = _hex_of(head.get("ChecksumSHA256"))
        if verified is not None:  # S3 checked it against the bytes on upload
            return StoredDigest(content_sha256=verified, size_bytes=size)
        # A store that does not keep checksums (MinIO, SeaweedFS, moto): read it back.
        return await asyncio.to_thread(self._stream_digest, key, max_bytes)

    def _stream_digest(self, key: str, max_bytes: int) -> StoredDigest:
        body = self.client.get_object(Bucket=self.bucket, Key=key)["Body"]
        sha, n = hashlib.sha256(), 0
        try:
            while chunk := body.read(_CHUNK):
                n += len(chunk)
                if n > max_bytes:
                    raise ObjectIntegrityError(f"{key} holds more than the {max_bytes} bytes allowed")
                sha.update(chunk)
        finally:
            body.close()
        return StoredDigest(content_sha256=sha.hexdigest(), size_bytes=n)

    async def copy(self, src: str, dst: str, *, content_sha256: str) -> None:
        try:
            await asyncio.to_thread(
                self.client.copy_object,
                Bucket=self.bucket,
                Key=dst,
                CopySource={"Bucket": self.bucket, "Key": src},
                MetadataDirective="REPLACE",
                Metadata={"sha256": content_sha256},
            )
        except Exception as exc:
            if _is_not_found(exc):
                raise ObjectNotFound(src) from exc
            raise ObjectTransient(str(exc)) from exc


def _hex_of(checksum: object) -> str | None:
    """A whole-object base64 SHA-256 as hex; None for none or a multipart (composite) one."""
    if not isinstance(checksum, str) or "-" in checksum:
        return None
    try:
        raw = base64.b64decode(checksum, validate=True)
    except (binascii.Error, ValueError):
        return None
    return raw.hex() if len(raw) == 32 else None



def _is_not_found(exc: BaseException) -> bool:
    name = type(exc).__name__
    resp = getattr(exc, "response", None)
    if isinstance(resp, dict):
        err = resp.get("Error") or {}
        if isinstance(err, dict) and err.get("Code") in {
            "404",
            "NoSuchKey",
            "NotFound",
        }:
            return True
    if name in {"NoSuchKey", "NotFound"}:
        return True
    msg = str(exc).lower()
    return "nosuchkey" in msg or "not found" in msg or "404" in msg


def build_boto3_s3_client(
    *,
    endpoint_url: str,
    access_key: str,
    secret_key: str,
    region: str = "us-east-1",
) -> Any:
    try:
        import boto3
        from botocore.client import Config
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(
            "boto3 required for S3 ObjectStorage (pip install 'yal-supervisor[s3]')"
        ) from exc
    return boto3.client(
        "s3",
        endpoint_url=endpoint_url,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        region_name=region,
        config=Config(signature_version="s3v4"),
    )
