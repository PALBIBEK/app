"""ObjectStorage implementations."""

from supervisor.seams.providers.storage.memory_object_storage import (
    MemoryObjectStorage,
    as_object_storage,
)
from supervisor.seams.providers.storage.object_storage_factory import (
    create_object_storage,
)
from supervisor.seams.providers.storage.s3_object_storage import (
    S3ClientPort,
    S3ObjectStorage,
    build_boto3_s3_client,
)

__all__ = [
    "MemoryObjectStorage",
    "S3ClientPort",
    "S3ObjectStorage",
    "as_object_storage",
    "build_boto3_s3_client",
    "create_object_storage",
]
