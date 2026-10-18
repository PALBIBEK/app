"""D9 blind acceptance — upload → rehydrate → sandbox read via supervisor HTTP."""

from __future__ import annotations

from uuid import uuid4

import pytest

from tests.d9_acceptance.conftest import bootstrap_scope


@pytest.mark.asyncio
async def test_t_upload_rehydrate_read_via_http(postgres_s3_stack) -> None:
    """LOCAL_DEMO #6: PUT durable file, session+scope, rehydrate, read in sandbox."""
    client = postgres_s3_stack["client"]
    state = postgres_s3_stack["state"]
    headers = postgres_s3_stack["headers"]
    scope = await bootstrap_scope(postgres_s3_stack)
    payload = b"d9-upload-rehydrate-e2e-v1"
    path = "uploaded/hello.txt"

    put = await client.put(
        f"/v1/scopes/{scope}/files/{path}",
        content=payload,
        headers={**headers, "Content-Type": "text/plain"},
        params={"mime_type": "text/plain"},
    )
    assert put.status_code == 200, put.text

    cr = await client.post(
        "/v1/sessions",
        json={
            "harness_version": "githi-v1",
            "runtime": "githi",
            "scope_id": scope,
        },
        headers=headers,
    )
    assert cr.status_code == 201, cr.text
    sid = cr.json()["session_id"]
    sandbox = state.manager._sandboxes[sid]
    assert sandbox is not None
    assert await sandbox.read_file(path) == payload

    await sandbox.write_file(path, b"stale-cache")
    assert await sandbox.read_file(path) == b"stale-cache"

    later = await client.put(
        f"/v1/scopes/{scope}/files/{path}",
        content=b"d9-upload-rehydrate-e2e-v2",
        headers={**headers, "Content-Type": "text/plain"},
        params={"mime_type": "text/plain", "expected_version": 1},
    )
    assert later.status_code == 200, later.text

    rh = await client.post(
        f"/v1/sessions/{sid}/workspace/rehydrate",
        headers=headers,
    )
    assert rh.status_code == 200, rh.text
    assert await sandbox.read_file(path) == b"d9-upload-rehydrate-e2e-v2"

    got = await client.get(
        f"/v1/scopes/{scope}/files/{path}",
        headers=headers,
    )
    assert got.status_code == 200
    assert got.content == b"d9-upload-rehydrate-e2e-v2"
