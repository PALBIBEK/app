"""Direct workspace uploads: start → the client PUTs to storage → complete (see workspace_uploads)."""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from types import SimpleNamespace

import boto3
import httpx
import pytest
from fastapi import FastAPI
from moto import mock_aws

from supervisor import workspace_uploads as uploads
from supervisor.api.workspace import router
from supervisor.seams.providers.storage.memory_object_storage import MemoryObjectStorage
from supervisor.seams.providers.storage.s3_object_storage import S3ObjectStorage
from supervisor.seams.providers.workspace.memory_workspace_store import MemoryWorkspaceStore

BUCKET = "yal-workspace"
SCOPE = "ws-1"
DATA = b"def add(a, b):\n    return a + b\n"
SHA = hashlib.sha256(DATA).hexdigest()


class _AllowAll:
    async def require(self, principal, scope_id, grant):
        return principal


@pytest.fixture
def s3() -> Iterator[object]:
    with mock_aws():
        client = boto3.client("s3", region_name="us-east-1")
        client.create_bucket(Bucket=BUCKET)
        yield client


def _api(store: MemoryWorkspaceStore) -> httpx.AsyncClient:
    app = FastAPI()
    app.include_router(router)
    app.state.supervisor = SimpleNamespace(workspace_store=store, authz=_AllowAll())
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        headers={"x-tenant-id": "t1", "x-principal-id": "p1"},
    )


def _start(path: str = "src/calc.py", data: bytes = DATA, **extra: object) -> dict[str, object]:
    return {"path": path, "size_bytes": len(data), "content_sha256": hashlib.sha256(data).hexdigest(),
            "mime_type": "text/x-python", **extra}


@pytest.mark.asyncio
async def test_a_direct_upload_lands_in_its_blob_and_is_recorded(s3) -> None:
    store = MemoryWorkspaceStore(objects=S3ObjectStorage(client=s3, bucket=BUCKET))
    async with _api(store) as api:
        started = (await api.post(f"/v1/scopes/{SCOPE}/uploads", json=_start())).json()
        assert started["status"] == "upload" and started["method"] == "PUT"
        assert f"/uploads/{SCOPE}/" in started["url"]  # a staging key, never the shared blob
        assert started["headers"]["x-amz-checksum-sha256"]
        ticket = uploads.verify(started["upload_token"], scope_id=SCOPE)
        s3.put_object(Bucket=BUCKET, Key=ticket.staging_key, Body=DATA)  # what the browser does

        done = await api.post(f"/v1/scopes/{SCOPE}/uploads/complete",
                              json={"upload_token": started["upload_token"]})
        assert done.status_code == 200, done.text
        assert done.json()["path"] == "src/calc.py" and done.json()["version"] == 1
        listed = (await api.get(f"/v1/scopes/{SCOPE}/files")).json()
        got = await api.get(f"/v1/scopes/{SCOPE}/files/src/calc.py")
    assert [f["content_sha256"] for f in listed] == [SHA]
    assert got.content == DATA
    keys = [o["Key"] for o in s3.list_objects_v2(Bucket=BUCKET).get("Contents", [])]
    assert keys == [uploads.blob_key(SHA)]  # staging object gone


@pytest.mark.asyncio
async def test_bytes_that_do_not_match_are_refused_and_removed(s3) -> None:
    store = MemoryWorkspaceStore(objects=S3ObjectStorage(client=s3, bucket=BUCKET))
    async with _api(store) as api:
        started = (await api.post(f"/v1/scopes/{SCOPE}/uploads", json=_start())).json()
        ticket = uploads.verify(started["upload_token"], scope_id=SCOPE)
        # A store that does not check the signed checksum lets other bytes through.
        s3.put_object(Bucket=BUCKET, Key=ticket.staging_key, Body=b"x" * len(DATA))
        done = await api.post(f"/v1/scopes/{SCOPE}/uploads/complete",
                              json={"upload_token": started["upload_token"]})
        listed = (await api.get(f"/v1/scopes/{SCOPE}/files")).json()
    assert done.status_code == 422 and "do not match" in done.json()["detail"]
    assert listed == []
    assert s3.list_objects_v2(Bucket=BUCKET).get("KeyCount") == 0


@pytest.mark.asyncio
async def test_completing_before_uploading_says_so(s3) -> None:
    store = MemoryWorkspaceStore(objects=S3ObjectStorage(client=s3, bucket=BUCKET))
    async with _api(store) as api:
        started = (await api.post(f"/v1/scopes/{SCOPE}/uploads", json=_start())).json()
        done = await api.post(f"/v1/scopes/{SCOPE}/uploads/complete",
                              json={"upload_token": started["upload_token"]})
    assert done.status_code == 409 and "nothing was uploaded" in done.json()["detail"]


@pytest.mark.asyncio
async def test_bytes_already_stored_are_committed_without_an_upload(s3) -> None:
    store = MemoryWorkspaceStore(objects=S3ObjectStorage(client=s3, bucket=BUCKET))
    await store.commit_blob("ws-other", "a.py", DATA)
    async with _api(store) as api:
        started = (await api.post(f"/v1/scopes/{SCOPE}/uploads", json=_start("b.py"))).json()
    assert started["status"] == "committed" and started["file"]["path"] == "b.py"
    assert started["upload_token"] is None


@pytest.mark.asyncio
async def test_a_ticket_is_bound_to_its_workspace_and_cannot_be_forged(s3) -> None:
    store = MemoryWorkspaceStore(objects=S3ObjectStorage(client=s3, bucket=BUCKET))
    async with _api(store) as api:
        token = (await api.post(f"/v1/scopes/{SCOPE}/uploads", json=_start())).json()["upload_token"]
        other = await api.post("/v1/scopes/ws-2/uploads/complete", json={"upload_token": token})
        body, _, mac = token.partition(".")
        forged = await api.post(f"/v1/scopes/{SCOPE}/uploads/complete",
                                json={"upload_token": body[:-2] + "AA." + mac})
    assert other.status_code == 400 and "another workspace" in other.json()["detail"]
    assert forged.status_code == 400


@pytest.mark.asyncio
async def test_limits_paths_and_versions_are_checked_before_any_url_is_given(s3, monkeypatch) -> None:
    monkeypatch.setenv("WORKSPACE_MAX_UPLOAD_BYTES", "10")
    store = MemoryWorkspaceStore(objects=S3ObjectStorage(client=s3, bucket=BUCKET))
    async with _api(store) as api:
        big = await api.post(f"/v1/scopes/{SCOPE}/uploads", json=_start())
        escape = await api.post(f"/v1/scopes/{SCOPE}/uploads", json=_start("../etc/passwd", b"x"))
        stale = await api.post(f"/v1/scopes/{SCOPE}/uploads", json=_start("n.txt", b"x", expected_version=3))
        proxied = await api.put(f"/v1/scopes/{SCOPE}/files/big.bin", content=b"y" * 11)
    assert big.status_code == 413 and "up to 10 bytes" in big.json()["detail"]
    assert escape.status_code == 400
    assert stale.status_code == 409
    assert proxied.status_code == 413  # the pass-through route has the same limit


@pytest.mark.asyncio
async def test_a_store_without_upload_urls_asks_for_the_pass_through_route() -> None:
    store = MemoryWorkspaceStore(objects=MemoryObjectStorage())
    async with _api(store) as api:
        started = (await api.post(f"/v1/scopes/{SCOPE}/uploads", json=_start())).json()
    assert started["status"] == "proxy" and started["upload_token"] is None


def test_an_expired_ticket_is_refused() -> None:
    ticket = uploads.new_ticket(scope_id=SCOPE, path="a", content_sha256=SHA, size_bytes=1,
                                mime_type="text/plain", expected_version=None, ttl_s=60)
    token = uploads.sign(ticket)
    assert uploads.verify(token, scope_id=SCOPE).path == "a"
    with pytest.raises(uploads.TicketInvalid, match="expired"):
        uploads.verify(token, scope_id=SCOPE, now=ticket.expires_at + 1)
