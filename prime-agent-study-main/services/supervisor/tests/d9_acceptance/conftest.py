"""Shared postgres + FakeS3 stack for D9 blind acceptance (no live Seaweed)."""

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

TENANT = "d9-tenant"
PRINCIPAL = "d9-owner"
HEADERS = {"X-Tenant-Id": TENANT, "X-Principal-Id": PRINCIPAL}
BUCKET = "yal-workspace-d9"


@pytest.fixture
def fake_s3() -> FakeS3Client:
    return FakeS3Client()


@pytest.fixture
def s3_objects(fake_s3: FakeS3Client) -> S3ObjectStorage:
    return S3ObjectStorage(client=fake_s3, bucket=BUCKET)


@pytest.fixture
async def postgres_s3_stack(
    tmp_path: Path, fake_s3: FakeS3Client, s3_objects: S3ObjectStorage
) -> AsyncIterator[dict[str, Any]]:
    """ASGI supervisor wired with postgres metadata + S3ObjectStorage(FakeS3Client)."""
    db_path = tmp_path / "d9_pg.db"
    url = f"sqlite+aiosqlite:///{db_path}"
    async with open_db(url) as (_, factory):
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
        )
        app = create_app(state=state)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            yield {
                "client": client,
                "state": state,
                "factory": factory,
                "store": store,
                "objects": s3_objects,
                "fake_s3": fake_s3,
                "hydrator": hydrator,
                "grants": grants,
                "authz": authz,
                "provider": provider,
                "app": app,
                "tmp_path": tmp_path,
                "db_url": url,
                "headers": HEADERS,
            }


async def bootstrap_scope(
    stack: dict[str, Any], scope_id: str | None = None
) -> str:
    """Ensure scope row + owner grants for PRINCIPAL."""
    scope = scope_id or f"d9-{uuid4().hex[:12]}"
    await stack["grants"].bootstrap_owner(PRINCIPAL, scope)
    return scope
