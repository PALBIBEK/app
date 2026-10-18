"""D9 blind acceptance — CAS conflict durable-wins rehydrate (manager path)."""

from __future__ import annotations

import pytest

from supervisor.seams.session_workspace import WorkspaceConflictError
from tests.d9_acceptance.conftest import bootstrap_scope
from tests.support.workspace_chaos import bump_durable_version


@pytest.mark.asyncio
async def test_t4_cas_conflict_manager_rehydrates_durable_wins(
    postgres_s3_stack,
) -> None:
    """Sync CAS conflict → fail closed → recover_cas_conflict replaces sandbox cache."""
    client = postgres_s3_stack["client"]
    state = postgres_s3_stack["state"]
    store = postgres_s3_stack["store"]
    headers = postgres_s3_stack["headers"]
    scope = await bootstrap_scope(postgres_s3_stack)
    path = "conflict/a.txt"
    durable_v1 = b"durable-v1"

    put = await client.put(
        f"/v1/scopes/{scope}/files/{path}",
        content=durable_v1,
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
    manager = state.manager
    sandbox = manager._sandboxes[sid]
    ws_state = manager._workspace[sid]
    assert sandbox is not None
    assert await sandbox.read_file(path) == durable_v1

    await sandbox.write_file(path, b"stale-sandbox-write")
    manager._workspace_svc.mark_dirty(ws_state)

    async def _bump(scope_id: str, p: str) -> None:
        await bump_durable_version(store, scope_id, p, b"durable-v2-wins")

    store.before_sync_cas = _bump
    try:
        with pytest.raises(WorkspaceConflictError) as exc_info:
            await manager._sync_workspace_fail_closed(sid, ws_state, sandbox)
        assert path in exc_info.value.conflicts
        assert await sandbox.read_file(path) == b"durable-v2-wins"
    finally:
        store.before_sync_cas = None
