"""WP-M5: scope-bound create → hydrate → tool write → sync."""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest

from supervisor.app import build_supervisor
from supervisor.db.engine import open_db
from supervisor.sandbox.local_process import LocalProcessProvider
from supervisor.seams.providers.grant_store_factory import (
    create_authz_service,
    create_grant_store,
)
from supervisor.seams.providers.memory_object_storage import MemoryObjectStorage
from supervisor.seams.providers.memory_workspace_store import MemoryWorkspaceStore
from supervisor.seams.workspace_hydrator import StoreWorkspaceHydrator
from tests.support.session_env import make_catalog


@pytest.mark.asyncio
async def test_scope_session_hydrate_write_sync(tmp_path: Path) -> None:
    objects = MemoryObjectStorage()
    store = MemoryWorkspaceStore(objects=objects)
    hydrator = StoreWorkspaceHydrator(store)
    scope_id = uuid4().hex
    await store.commit_blob(scope_id, "seed.txt", b"seed-bytes")

    grant_store = create_grant_store("memory")
    authz = create_authz_service(store=grant_store)
    provider = LocalProcessProvider(
        base_dir=tmp_path / "sandboxes", hydrator=hydrator
    )

    async with open_db(f"sqlite+aiosqlite:///{tmp_path / 'm5.db'}") as (_, factory):
        state = build_supervisor(
            session_factory=factory,
            catalog=make_catalog(),
            blob_dir=tmp_path / "blobs",
            sandbox_provider=provider,
            authz=authz,
            grant_store=grant_store,
        )
        sid = await state.manager.create(
            tenant_id="t1",
            harness_version="githi-v1",
            runtime_name="githi",
            scope_id=scope_id,
            principal_id="t1",
        )
        ws = state.manager._workspace[sid]
        assert ws.ready is True
        sandbox = state.manager._sandboxes[sid]
        assert sandbox is not None
        tree = sandbox.as_tree_io()
        assert await tree.read_bytes("seed.txt") == b"seed-bytes"

        runtime = state.manager._registry.get(sid)
        assert runtime is not None
        provider_tools = runtime.seams.tool_provider
        from supervisor.seams.types import ToolInvokeContext

        result = provider_tools.invoke(
            ToolInvokeContext(),
            "write",
            {"file_path": "out.txt", "content": "from-tool"},
        )
        assert result.ok is True
        assert ws.dirty is True

        report = await state.manager._workspace_svc.sync_if_dirty(ws, sandbox)
        assert report is not None
        assert "out.txt" in report.written
        assert ws.dirty is False
        row = await store.stat(scope_id, "out.txt")
        assert row is not None
        assert await objects.get(row.object_key) == b"from-tool"

        await state.manager.terminate(sid)
