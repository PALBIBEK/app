"""WP-M1: S3ObjectStorage adapter tests with injectable fake client (no network)."""

from __future__ import annotations

from typing import Any

import pytest

from supervisor.seams.object_storage import ObjectNotFound, ObjectStorage
from supervisor.seams.providers.object_storage_factory import create_object_storage
from supervisor.seams.providers.s3_object_storage import S3ObjectStorage


class _FakeBody:
    def __init__(self, data: bytes) -> None:
        self._data = data

    def read(self) -> bytes:
        return self._data


class FakeS3Client:
    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], bytes] = {}
        self.meta: dict[tuple[str, str], dict[str, str]] = {}

    def put_object(self, *, Bucket: str, Key: str, Body: bytes, **kwargs: Any) -> dict:
        self.objects[(Bucket, Key)] = Body
        self.meta[(Bucket, Key)] = dict(kwargs.get("Metadata") or {})
        return {}

    def get_object(self, *, Bucket: str, Key: str) -> dict:
        try:
            data = self.objects[(Bucket, Key)]
        except KeyError as exc:
            err = Exception("NoSuchKey")
            err.response = {"Error": {"Code": "NoSuchKey"}}  # type: ignore[attr-defined]
            raise err from exc
        return {"Body": _FakeBody(data)}

    def head_object(self, *, Bucket: str, Key: str) -> dict:
        try:
            data = self.objects[(Bucket, Key)]
        except KeyError as exc:
            err = Exception("404")
            err.response = {"Error": {"Code": "404"}}  # type: ignore[attr-defined]
            raise err from exc
        return {
            "ContentLength": len(data),
            "Metadata": self.meta.get((Bucket, Key), {}),
        }

    def delete_object(self, *, Bucket: str, Key: str) -> dict:
        self.objects.pop((Bucket, Key), None)
        self.meta.pop((Bucket, Key), None)
        return {}

    def generate_presigned_url(
        self, ClientMethod: str, Params: dict[str, Any], ExpiresIn: int
    ) -> str:
        key = Params["Key"]
        return f"https://example.test/{ClientMethod}/{key}?ttl={ExpiresIn}"


@pytest.mark.asyncio
async def test_s3_adapter_roundtrip_with_fake_client() -> None:
    client = FakeS3Client()
    store = S3ObjectStorage(client=client, bucket="yal-workspace")
    assert isinstance(store, ObjectStorage)

    put = await store.put("blobs/ab/abc", b"payload", content_type="text/plain")
    assert put.size_bytes == 7
    assert await store.exists("blobs/ab/abc") is True
    assert await store.get("blobs/ab/abc") == b"payload"
    head = await store.head("blobs/ab/abc")
    assert head.exists is True
    assert head.content_sha256 == put.content_sha256

    url = await store.create_download_url("blobs/ab/abc", ttl_s=60)
    assert "get_object" in url.url
    up = await store.create_upload_url("blobs/ab/new", ttl_s=30)
    assert up.method == "PUT"

    await store.delete("blobs/ab/abc")
    assert await store.exists("blobs/ab/abc") is False
    with pytest.raises(ObjectNotFound):
        await store.get("blobs/ab/abc")


def test_factory_memory_default() -> None:
    store = create_object_storage("memory")
    assert isinstance(store, ObjectStorage)


def test_factory_s3_with_injected_client() -> None:
    store = create_object_storage("seaweed", client=FakeS3Client(), bucket="b")
    assert isinstance(store, S3ObjectStorage)


def test_factory_unknown_raises() -> None:
    with pytest.raises(ValueError, match="unknown OBJECT_STORAGE"):
        create_object_storage("mystery")
