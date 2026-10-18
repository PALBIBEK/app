"""Shared postgres grant stack for D11 blind acceptance."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any
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
from supervisor.seams.providers.s3_object_storage import S3ObjectStorage
from supervisor.seams.providers.workspace_store_factory import create_workspace_store
from supervisor.seams.workspace_hydrator import StoreWorkspaceHydrator
from tests.support.session_env import make_catalog
from tests.unit.test_s3_object_storage import FakeS3Client

TENANT = "d11-tenant"
OWNER = "d11-owner"
MEMBER = "d11-member"
STRANGER = "d11-stranger"
HEADERS = {"X-Tenant-Id": TENANT, "X-Principal-Id": OWNER}
BUCKET = "yal-workspace-d11"


def headers_for(principal_id: str) -> dict[str, str]:
    return {"X-Tenant-Id": TENANT, "X-Principal-Id": principal_id}


@pytest.fixture
def fake_s3() -> FakeS3Client:
    return FakeS3Client()


@pytest.fixture
def s3_objects(fake_s3: FakeS3Client) -> S3ObjectStorage:
    return S3ObjectStorage(client=fake_s3, bucket=BUCKET)


async def _build_stack(
    tmp_path: Path,
    factory,
    *,
    s3_objects: S3ObjectStorage,
    auth_required: bool = False,
    service_jwt_secret: str | None = None,
) -> dict[str, Any]:
    store = create_workspace_store(
        "postgres", objects=s3_objects, session_factory=factory
    )
    hydrator = StoreWorkspaceHydrator(store)
    grants = create_grant_store("postgres", session_factory=factory)
    authz = create_authz_service(store=grants)
    provider = LocalProcessProvider(
        base_dir=tmp_path / "sandboxes", hydrator=hydrator
    )
    state = build_supervisor(
        session_factory=factory,
        catalog=make_catalog(),
        blob_dir=tmp_path / "blobs",
        sandbox_provider=provider,
        authz=authz,
        grant_store=grants,
        workspace_store=store,
        auth_required=auth_required,
        service_jwt_secret=service_jwt_secret,
    )
    app = create_app(state=state)
    transport = ASGITransport(app=app)
    client = AsyncClient(transport=transport, base_url="http://test")
    return {
        "client": client,
        "state": state,
        "factory": factory,
        "store": store,
        "objects": s3_objects,
        "grants": grants,
        "authz": authz,
        "provider": provider,
        "app": app,
        "tmp_path": tmp_path,
    }


@pytest.fixture
async def postgres_grant_stack(
    tmp_path: Path, fake_s3: FakeS3Client, s3_objects: S3ObjectStorage
) -> AsyncIterator[dict[str, Any]]:
    db_path = tmp_path / "d11_pg.db"
    url = f"sqlite+aiosqlite:///{db_path}"
    async with open_db(url) as (_, factory):
        stack = await _build_stack(tmp_path, factory, s3_objects=s3_objects)
        stack["db_url"] = url
        stack["headers"] = HEADERS
        try:
            yield stack
        finally:
            await stack["client"].aclose()


async def bootstrap_scope(
    stack: dict[str, Any], scope_id: str | None = None, *, owner: str = OWNER
) -> str:
    """Ensure ScopeRow + owner grants (existing scope — no re-bootstrap on attach)."""
    from supervisor.db.models import ScopeRow

    scope = scope_id or f"d11-{uuid4().hex[:12]}"
    factory = stack["factory"]
    async with factory() as db:
        if await db.get(ScopeRow, scope) is None:
            db.add(
                ScopeRow(
                    id=scope,
                    type="personal",
                    owner_principal_id=owner,
                    name=scope,
                )
            )
            await db.commit()
    await stack["grants"].bootstrap_owner(owner, scope)
    return scope


async def reopen_stack(
    stack: dict[str, Any],
) -> dict[str, Any]:
    """Simulate supervisor restart — new app wiring, same session_factory + DB."""
    await stack["client"].aclose()
    tmp_path = stack["tmp_path"]
    factory = stack["factory"]
    s3_objects = stack["objects"]
    new_stack = await _build_stack(
        tmp_path,
        factory,
        s3_objects=s3_objects,
        auth_required=stack["state"].auth_required,
        service_jwt_secret=stack["state"].service_jwt_secret,
    )
    new_stack["db_url"] = stack.get("db_url")
    new_stack["headers"] = stack.get("headers", HEADERS)
    return new_stack
