"""WP-M7 HTTP — foreign scope_id attach returns 403."""

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
from supervisor.seams.workspace_hydrator import StoreWorkspaceHydrator
from tests.support.session_env import make_catalog

OWNER = {"X-Tenant-Id": "t1", "X-Principal-Id": "owner"}
EVIL = {"X-Tenant-Id": "evil", "X-Principal-Id": "evil"}


@pytest.mark.asyncio
async def test_http_foreign_scope_attach_403(tmp_path: Path) -> None:
    objects = MemoryObjectStorage()
    store = MemoryWorkspaceStore(objects=objects)
    hydrator = StoreWorkspaceHydrator(store)
    grant_store = create_grant_store("memory")
    authz = create_authz_service(store=grant_store)
    provider = LocalProcessProvider(
        base_dir=tmp_path / "sandboxes", hydrator=hydrator
    )
    scope_id = uuid4().hex

    async with open_db(f"sqlite+aiosqlite:///{tmp_path / 'm7i.db'}") as (_, factory):
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
            ok = await client.post(
                "/v1/sessions",
                json={
                    "harness_version": "githi-v1",
                    "runtime": "githi",
                    "scope_id": scope_id,
                },
                headers=OWNER,
            )
            assert ok.status_code == 201, ok.text

            hijack = await client.post(
                "/v1/sessions",
                json={
                    "harness_version": "githi-v1",
                    "runtime": "githi",
                    "scope_id": scope_id,
                },
                headers=EVIL,
            )
            assert hijack.status_code == 403, hijack.text
            assert not await grant_store.is_member("evil", scope_id)
