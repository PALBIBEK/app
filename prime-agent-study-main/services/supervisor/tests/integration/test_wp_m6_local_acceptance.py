"""WP-M6 local acceptance via sessions API — file survives restart."""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from supervisor.app import build_supervisor
from supervisor.db.engine import open_db
from supervisor.main import create_app
from supervisor.sandbox.local_process import LocalProcessProvider
from supervisor.seams.providers.grant_store_factory import (
    create_authz_service,
    create_grant_store,
)
from supervisor.seams.providers.memory_object_storage import MemoryObjectStorage
from supervisor.seams.providers.memory_workspace_store import MemoryWorkspaceStore
from supervisor.seams.types import ToolInvokeContext
from supervisor.seams.workspace_hydrator import StoreWorkspaceHydrator
from tests.support.session_env import make_catalog

HEADERS = {"X-Tenant-Id": "t1", "X-Principal-Id": "t1"}


@pytest.mark.asyncio
async def test_api_scope_session_write_survives_sandbox_restart(
    tmp_path: Path,
) -> None:
    objects = MemoryObjectStorage()
    store = MemoryWorkspaceStore(objects=objects)
    hydrator = StoreWorkspaceHydrator(store)
    grant_store = create_grant_store("memory")
    authz = create_authz_service(store=grant_store)
    provider = LocalProcessProvider(
        base_dir=tmp_path / "sandboxes", hydrator=hydrator
    )
    scope_id = uuid4().hex

    async with open_db(f"sqlite+aiosqlite:///{tmp_path / 'm6.db'}") as (_, factory):
        state = build_supervisor(
            session_factory=factory,
            catalog=make_catalog(),
            blob_dir=tmp_path / "blobs",
            sandbox_provider=provider,
            authz=authz,
            grant_store=grant_store,
        )
        app = create_app(state=state)
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            cr = await client.post(
                "/v1/sessions",
                json={
                    "harness_version": "githi-v1",
                    "runtime": "githi",
                    "scope_id": scope_id,
                },
                headers=HEADERS,
            )
            assert cr.status_code == 201, cr.text
            sid = cr.json()["session_id"]

            ws = state.manager._workspace[sid]
            assert ws.ready is True
            assert ws.scope_id == scope_id

            rt = state.manager._registry.get(sid)
            sb = state.manager._sandboxes[sid]
            assert rt is not None and sb is not None
            w = rt.seams.tool_provider.invoke(
                ToolInvokeContext(),
                "write",
                {"file_path": "report.md", "content": "# ok\n"},
            )
            assert w.ok is True
            await state.manager._workspace_svc.sync_if_dirty(ws, sb)
            row = await store.stat(scope_id, "report.md")
            assert row is not None
            assert await objects.get(row.object_key) == b"# ok\n"

            # Chaos: drop live sandbox; rehydrate into a fresh one.
            ws.ready = False
            fresh = await provider.create()
            state.manager._sandboxes[sid] = fresh
            await state.manager._workspace_svc.ensure_hydrated(ws, fresh, force=True)
            assert await fresh.as_tree_io().read_bytes("report.md") == b"# ok\n"

            # Cross-principal cannot attach to stranger scope (WP-M7 IDOR fix)
            other = await client.post(
                "/v1/sessions",
                json={
                    "harness_version": "githi-v1",
                    "runtime": "githi",
                    "scope_id": scope_id,
                },
                headers={"X-Tenant-Id": "evil", "X-Principal-Id": "evil"},
            )
            assert other.status_code == 403, other.text

            from supervisor.seams.authz import NotScopeMember, Principal
            from supervisor.seams.authorized_workspace import authorized_hydrate

            with pytest.raises(NotScopeMember):
                await authorized_hydrate(
                    authz,
                    hydrator,
                    Principal(id="nope", tenant_id="t1"),
                    scope_id,
                    fresh.as_tree_io(),
                )

            dr = await client.delete(f"/v1/sessions/{sid}", headers=HEADERS)
            assert dr.status_code == 204
