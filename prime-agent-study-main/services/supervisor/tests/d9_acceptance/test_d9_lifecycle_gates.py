"""D9 blind acceptance — lifecycle gates (T6, T13)."""

from __future__ import annotations

from uuid import uuid4

import pytest

from supervisor.seams.session_workspace import WorkspaceNotReadyError
from supervisor.seams.workspace_store import HydrateIncomplete
from tests.d9_acceptance.conftest import bootstrap_scope
from tests.support.workspace_chaos import CrashyTreeIO


@pytest.mark.asyncio
async def test_t13_session_without_scope_id_no_hydrate(postgres_s3_stack) -> None:
    """Session without scope_id skips hydrate; workspace state stays inert."""
    client = postgres_s3_stack["client"]
    state = postgres_s3_stack["state"]
    headers = postgres_s3_stack["headers"]

    resp = await client.post(
        "/v1/sessions",
        json={"harness_version": "githi-v1", "runtime": "githi"},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    sid = resp.json()["session_id"]

    ws = state.manager._workspace[sid]
    assert ws.scope_id is None
    assert ws.principal is None
    assert ws.ready is False

    sandbox = state.manager._sandboxes.get(sid)
    assert sandbox is not None


@pytest.mark.asyncio
async def test_t6_hydrate_incomplete_blocks_invoke(postgres_s3_stack) -> None:
    """R8: partial hydrate → ready=False; tool invoke returns 409."""
    store = postgres_s3_stack["store"]
    client = postgres_s3_stack["client"]
    state = postgres_s3_stack["state"]
    headers = postgres_s3_stack["headers"]
    scope = await bootstrap_scope(postgres_s3_stack)

    await store.commit_blob(scope, "a.txt", b"one", expected_version=0)
    await store.commit_blob(scope, "b.txt", b"two", expected_version=0)
    crashy = CrashyTreeIO(fail_after_n_writes=1)
    with pytest.raises(HydrateIncomplete):
        await store.hydrate(scope, crashy)

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
    ws = state.manager._workspace[sid]
    assert ws.ready is True

    ws.ready = False
    svc = state.manager._workspace_svc
    with pytest.raises(WorkspaceNotReadyError):
        svc.assert_ready(ws)

    invoke = await client.post(
        f"/v1/sessions/{sid}/tools/invoke",
        json={"name": "read", "arguments": {"file_path": "a.txt"}},
        headers=headers,
    )
    assert invoke.status_code == 409, invoke.text
    assert "not ready" in invoke.text.lower()
