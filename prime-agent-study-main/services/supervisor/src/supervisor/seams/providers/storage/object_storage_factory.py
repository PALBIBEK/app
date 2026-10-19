"""ObjectStorage factory — sole place backend names appear (Factory pattern)."""

from __future__ import annotations

import os
from typing import Any

from supervisor.seams.services.object_storage import ObjectStorage
from supervisor.seams.providers.storage.memory_object_storage import MemoryObjectStorage


def create_object_storage(
    name: str | None = None,
    *,
    client: Any | None = None,
    bucket: str | None = None,
) -> ObjectStorage:
    """Select store from ``OBJECT_STORAGE`` or an explicit name.

    Names: ``memory`` | ``s3`` | ``seaweed`` (alias of s3)
    """
    chosen = (name or os.environ.get("OBJECT_STORAGE") or "memory").strip().lower()
    if chosen in ("memory", "mem", "fake", ""):
        return MemoryObjectStorage()
    if chosen in ("s3", "seaweed", "seaweedfs", "minio"):
        from supervisor.seams.providers.storage.s3_object_storage import (
            S3ObjectStorage,
            build_boto3_s3_client,
        )

        bucket_name = bucket or os.environ.get("OBJECT_STORAGE_BUCKET") or "yal-workspace"
        if client is None:
            client = build_boto3_s3_client(
                endpoint_url=os.environ.get(
                    "OBJECT_STORAGE_ENDPOINT", "http://127.0.0.1:8333"
                ),
                access_key=os.environ.get("OBJECT_STORAGE_ACCESS_KEY", "any"),
                secret_key=os.environ.get("OBJECT_STORAGE_SECRET_KEY", "any"),
                region=os.environ.get("OBJECT_STORAGE_REGION", "us-east-1"),
            )
        return S3ObjectStorage(client=client, bucket=bucket_name)
    raise ValueError(f"unknown OBJECT_STORAGE={chosen!r}")
