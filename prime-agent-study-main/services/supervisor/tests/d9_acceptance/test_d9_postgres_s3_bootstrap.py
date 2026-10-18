"""D9 blind acceptance — postgres + S3ObjectStorage bootstrap paths (T2, T11)."""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest

from supervisor.db.engine import open_db
from supervisor.sandbox.local_process import LocalProcessProvider
from supervisor.seams.providers.object_storage_factory import create_object_storage
from supervisor.seams.providers.s3_object_storage import S3ObjectStorage
from supervisor.seams.providers.workspace_store_factory import (
    create_object_storage_for_workspace,
    create_workspace_store,
)
from tests.d9_acceptance.conftest import BUCKET
from tests.unit.test_s3_object_storage import FakeS3Client


@pytest.mark.asyncio
async def test_t2_postgres_s3_bootstrap_restart_hydrate(
    tmp_path: Path, fake_s3: FakeS3Client
) -> None:
    """Postgres metadata + FakeS3 bytes survive new DB session; hydrate matches sha."""
    db_path = tmp_path / "t2.db"
    url = f"sqlite+aiosqlite:///{db_path}"
    objects = S3ObjectStorage(client=fake_s3, bucket=BUCKET)
    scope = f"t2-{uuid4().hex[:12]}"
    payload = b"d9-t2-postgres-s3-restart-v1"

    async with open_db(url) as (_, factory):
        store = create_workspace_store(
            "postgres", objects=objects, session_factory=factory
        )
        rec = await store.commit_blob(scope, "notes/persist.txt", payload)
        assert rec.version == 1
        assert await objects.get(rec.object_key) == payload
        assert (BUCKET, rec.object_key) in fake_s3.objects

    async with open_db(url) as (_, factory2):
        store2 = create_workspace_store(
            "postgres", objects=objects, session_factory=factory2
        )
        provider = LocalProcessProvider(base_dir=tmp_path / "sandboxes")
        sandbox = await provider.create()
        report = await store2.hydrate(scope, sandbox.as_tree_io())
        assert "notes/persist.txt" in report.written
        assert await sandbox.read_file("notes/persist.txt") == payload
        live = await store2.stat(scope, "notes/persist.txt")
        assert live is not None
        assert await objects.get(live.object_key) == payload


def test_t11_bootstrap_object_storage_factory_seaweed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """create_object_storage_for_workspace(seaweed) uses injectable client — no network."""
    client = FakeS3Client()
    monkeypatch.setenv("OBJECT_STORAGE", "seaweed")
    monkeypatch.setenv("OBJECT_STORAGE_BUCKET", BUCKET)
    monkeypatch.delenv("ENV", raising=False)
    monkeypatch.setattr(
        "supervisor.seams.providers.s3_object_storage.build_boto3_s3_client",
        lambda **kwargs: client,
    )

    store = create_object_storage("seaweed", client=client, bucket=BUCKET)
    assert isinstance(store, S3ObjectStorage)

    workspace_objs = create_object_storage_for_workspace()
    assert isinstance(workspace_objs, S3ObjectStorage)


@pytest.mark.asyncio
async def test_t11_factory_put_visible_in_fake_bucket(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Compose seaweed path: factory put lands in bucket (FakeS3Client)."""
    client = FakeS3Client()
    monkeypatch.setenv("OBJECT_STORAGE", "seaweed")
    monkeypatch.setenv("OBJECT_STORAGE_BUCKET", BUCKET)
    store = create_object_storage("seaweed", client=client, bucket=BUCKET)
    put = await store.put("blobs/de/adbeef", b"seaweed-bytes")
    assert (BUCKET, "blobs/de/adbeef") in client.objects
    assert client.objects[(BUCKET, "blobs/de/adbeef")] == b"seaweed-bytes"
