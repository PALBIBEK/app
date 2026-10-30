"""A workspace stores at most ``WORKSPACE_MAX_SCOPE_BYTES``; past it, new bytes are refused.

There was no limit on what a workspace keeps (every version of every file): only each file
was limited, so uploading again and again filled the storage.
"""

from __future__ import annotations

import hashlib
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import boto3
import httpx
import pytest
from fastapi import FastAPI
from moto import mock_aws

from supervisor import workspace_uploads as uploads
from supervisor.api.workspace import router
from supervisor.db.engine import open_db
from supervisor.seams.providers.storage.memory_object_storage import MemoryObjectStorage
from supervisor.seams.providers.storage.s3_object_storage import S3ObjectStorage
from supervisor.seams.providers.workspace.postgres_workspace_store import PostgresWorkspaceStore
from supervisor.seams.providers.workspace.sync import three_way_sync
from supervisor.seams.providers.workspace.workspace_store_factory import (
    DEFAULT_MAX_SCOPE_BYTES,
    max_scope_bytes,
)
from supervisor.seams.services import WorkspaceQuotaExceeded

pytestmark = pytest.mark.asyncio

SCOPE = "ws-1"
LIMIT = 100
BUCKET = "yal-workspace"


@pytest.fixture
async def factory(tmp_path: Path) -> AsyncIterator[Any]:
    async with open_db(f"sqlite+aiosqlite:///{tmp_path / 'q.db'}") as (_, factory):
        yield factory


async def test_new_bytes_past_the_limit_are_refused_and_not_stored(factory: Any) -> None:
    objects = MemoryObjectStorage()
    store = PostgresWorkspaceStore(factory, objects, max_scope_bytes=LIMIT)
    await store.commit_blob(SCOPE, "a.txt", b"a" * 60)
    await store.commit_blob(SCOPE, "copy.txt", b"a" * 60)  # the same bytes: stored once, fits
    assert await store.stored_bytes(SCOPE) == 60
    with pytest.raises(WorkspaceQuotaExceeded) as refused:
        await store.commit_blob(SCOPE, "b.txt", b"b" * 50)
    assert (refused.value.stored, refused.value.adding, refused.value.limit) == (60, 50, LIMIT)
    assert len(objects._objects) == 1  # the refused bytes were never written
    assert await store.stat(SCOPE, "b.txt") is None
    await store.commit_blob(SCOPE, "c.txt", b"c" * 40)  # exactly to the limit
    await store.commit_blob("ws-2", "b.txt", b"b" * 50)  # another workspace has its own room


async def test_without_a_limit_anything_is_kept(factory: Any) -> None:
    store = PostgresWorkspaceStore(factory, MemoryObjectStorage())
    await store.commit_blob(SCOPE, "big.bin", b"x" * 10_000)
    assert await store.stored_bytes(SCOPE) == 10_000


async def test_a_sync_saves_what_fits_and_reports_the_rest(factory: Any) -> None:
    store = PostgresWorkspaceStore(factory, MemoryObjectStorage(), max_scope_bytes=LIMIT)

    class Tree:  # synced in path order: what comes after a file that did not fit still may
        def __init__(self) -> None:
            self.files = {"1-small.txt": b"s" * 30, "2-huge.bin": b"h" * 90, "3-notes.md": b"n" * 20}

        async def list_paths(self) -> list[str]:
            return list(self.files)

        async def read_bytes(self, path: str) -> bytes:
            return self.files[path]

    report = await three_way_sync(store, SCOPE, Tree(), base={})
    assert report.refused == ("2-huge.bin",)
    assert report.written == ("1-small.txt", "3-notes.md")


async def test_the_limit_defaults_to_ten_gibibytes_and_zero_lifts_it(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("WORKSPACE_MAX_SCOPE_BYTES", raising=False)
    assert max_scope_bytes() == DEFAULT_MAX_SCOPE_BYTES == 10 * 1024**3
    monkeypatch.setenv("WORKSPACE_MAX_SCOPE_BYTES", "0")
    assert max_scope_bytes() is None


class _AllowAll:
    async def require(self, principal: object, scope_id: str, grant: object) -> object:
        return principal


@pytest.fixture
def s3() -> Iterator[Any]:
    with mock_aws():
        client = boto3.client("s3", region_name="us-east-1")
        client.create_bucket(Bucket=BUCKET)
        yield client


def _api(store: PostgresWorkspaceStore) -> httpx.AsyncClient:
    app = FastAPI()
    app.include_router(router)
    app.state.supervisor = SimpleNamespace(workspace_store=store, authz=_AllowAll())
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://t", headers={"x-tenant-id": "t1", "x-principal-id": "p1"}
    )


def _start(path: str, data: bytes) -> dict[str, object]:
    return {"path": path, "size_bytes": len(data), "content_sha256": hashlib.sha256(data).hexdigest()}


async def test_an_upload_that_cannot_fit_is_refused_before_it_is_sent(factory: Any, s3: Any) -> None:
    store = PostgresWorkspaceStore(factory, S3ObjectStorage(client=s3, bucket=BUCKET), max_scope_bytes=LIMIT)
    await store.commit_blob(SCOPE, "a.txt", b"a" * 60)
    async with _api(store) as api:
        refused = await api.post(f"/v1/scopes/{SCOPE}/uploads", json=_start("b.bin", b"b" * 50))
        fits = await api.post(f"/v1/scopes/{SCOPE}/uploads", json=_start("c.bin", b"c" * 40))
    assert refused.status_code == 413
    assert refused.json()["detail"]["error_code"] == "WORKSPACE_QUOTA_EXCEEDED"
    assert fits.status_code == 200 and fits.json()["status"] == "upload"


async def test_a_workspace_that_filled_up_during_an_upload_refuses_it_and_drops_the_bytes(
    factory: Any, s3: Any
) -> None:
    store = PostgresWorkspaceStore(factory, S3ObjectStorage(client=s3, bucket=BUCKET), max_scope_bytes=LIMIT)
    data = b"d" * 50
    async with _api(store) as api:
        started = (await api.post(f"/v1/scopes/{SCOPE}/uploads", json=_start("d.bin", data))).json()
        ticket = uploads.verify(started["upload_token"], scope_id=SCOPE)
        s3.put_object(Bucket=BUCKET, Key=ticket.staging_key, Body=data)  # what the browser does
        await store.commit_blob(SCOPE, "meanwhile.bin", b"m" * 60)  # someone else filled it
        done = await api.post(f"/v1/scopes/{SCOPE}/uploads/complete", json={"upload_token": started["upload_token"]})
    assert done.status_code == 413
    keys = [o["Key"] for o in s3.list_objects_v2(Bucket=BUCKET).get("Contents", [])]
    assert len(keys) == 1  # only the file that fitted; the staged upload is gone


async def test_a_session_whose_files_do_not_fit_is_told_and_saves_them_once_there_is_room(factory: Any) -> None:
    from supervisor.seams.providers.workspace.memory_workspace_store import MemoryTreeIO
    from supervisor.seams.services import Principal
    from supervisor.seams.services.session_workspace import (
        SessionWorkspaceService,
        SessionWorkspaceState,
        WorkspaceFullError,
    )
    from supervisor.seams.services.workspace_hydrator import StoreWorkspaceHydrator

    class Sandbox:
        def __init__(self) -> None:
            self.tree = MemoryTreeIO()

        def as_tree_io(self) -> MemoryTreeIO:
            return self.tree

    store = PostgresWorkspaceStore(factory, MemoryObjectStorage(), max_scope_bytes=LIMIT)
    await store.commit_blob(SCOPE, "old.bin", b"o" * 60)
    service = SessionWorkspaceService(authz=_AllowAll(), hydrator=StoreWorkspaceHydrator(store))  # type: ignore[arg-type]
    state, box = SessionWorkspaceState(scope_id=SCOPE, principal=Principal(id="alice", tenant_id="t1")), Sandbox()
    await service.ensure_hydrated(state, box)  # type: ignore[arg-type]

    box.tree.files["report.bin"] = b"r" * 50
    service.mark_dirty(state)
    with pytest.raises(WorkspaceFullError) as full:
        await service.sync_if_dirty(state, box)  # type: ignore[arg-type]
    assert full.value.paths == ("report.bin",)
    assert state.dirty  # kept to try again

    store.max_scope_bytes = 120  # the operator gives the workspace more room
    report = await service.sync_if_dirty(state, box)  # type: ignore[arg-type]
    assert report is not None and report.written == ("report.bin",)
