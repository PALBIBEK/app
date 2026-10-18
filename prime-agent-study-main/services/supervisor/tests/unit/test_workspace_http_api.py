"""Stage 2: workspace HTTP list/get/put/delete/presign + grant gates."""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from supervisor.app import build_supervisor
from supervisor.db.engine import open_db
from supervisor.main import create_app
from supervisor.seams.authz import Grant
from supervisor.seams.providers.grant_store_factory import (
    create_authz_service,
    create_grant_store,
)
from supervisor.seams.providers.memory_object_storage import MemoryObjectStorage
from supervisor.seams.providers.workspace_store_factory import create_workspace_store
from tests.support.session_env import make_catalog

OWNER = {"X-Tenant-Id": "t1", "X-Principal-Id": "owner"}
EVIL = {"X-Tenant-Id": "evil", "X-Principal-Id": "evil"}


@pytest.mark.asyncio
async def test_workspace_http_crud_and_presign(tmp_path: Path) -> None:
    objects = MemoryObjectStorage()
    async with open_db(f"sqlite+aiosqlite:///{tmp_path / 'ws.db'}") as (_, factory):
        store = create_workspace_store(
            "postgres", objects=objects, session_factory=factory
        )
        grants = create_grant_store("postgres", session_factory=factory)
        authz = create_authz_service(store=grants)
        scope = uuid4().hex
        await grants.bootstrap_owner("owner", scope)

        state = build_supervisor(
            session_factory=factory,
            catalog=make_catalog(),
            blob_dir=tmp_path / "blobs",
            authz=authz,
            grant_store=grants,
            workspace_store=store,
        )
        app = create_app(state=state)
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            put = await client.put(
                f"/v1/scopes/{scope}/files/docs/readme.md",
                content=b"# hello\n",
                headers={**OWNER, "Content-Type": "text/markdown"},
                params={"mime_type": "text/markdown"},
            )
            assert put.status_code == 200, put.text
            assert put.json()["path"] == "docs/readme.md"
            assert put.json()["version"] == 1

            listing = await client.get(
                f"/v1/scopes/{scope}/files", headers=OWNER
            )
            assert listing.status_code == 200
            paths = [r["path"] for r in listing.json()]
            assert "docs/readme.md" in paths

            got = await client.get(
                f"/v1/scopes/{scope}/files/docs/readme.md", headers=OWNER
            )
            assert got.status_code == 200
            assert got.content == b"# hello\n"

            meta = await client.get(
                f"/v1/scopes/{scope}/files/docs/readme.md",
                headers=OWNER,
                params={"meta": "true"},
            )
            assert meta.status_code == 200
            assert meta.json()["content_sha256"]

            # Read-only stranger → 404
            denied = await client.get(
                f"/v1/scopes/{scope}/files", headers=EVIL
            )
            assert denied.status_code == 404

            # Presign requires ARTIFACT_DOWNLOAD (owner has all grants)
            dl = await client.post(
                f"/v1/scopes/{scope}/files/docs/readme.md/download",
                headers=OWNER,
                params={"ttl_s": 60},
            )
            assert dl.status_code == 200, dl.text
            assert dl.json()["url"].startswith("memory://download/")
            assert dl.json()["method"] == "GET"

            # Member without download grant
            await grants.add_member("reader", scope)
            await grants.grant("reader", scope, Grant.WORKSPACE_READ)
            no_dl = await client.post(
                f"/v1/scopes/{scope}/files/docs/readme.md/download",
                headers={"X-Tenant-Id": "t1", "X-Principal-Id": "reader"},
            )
            assert no_dl.status_code == 404

            deleted = await client.delete(
                f"/v1/scopes/{scope}/files/docs/readme.md", headers=OWNER
            )
            assert deleted.status_code == 204
            missing = await client.get(
                f"/v1/scopes/{scope}/files/docs/readme.md", headers=OWNER
            )
            assert missing.status_code == 404


@pytest.mark.asyncio
async def test_workspace_http_path_escape_400(tmp_path: Path) -> None:
    objects = MemoryObjectStorage()
    async with open_db(f"sqlite+aiosqlite:///{tmp_path / 'ws2.db'}") as (_, factory):
        store = create_workspace_store(
            "postgres", objects=objects, session_factory=factory
        )
        grants = create_grant_store("postgres", session_factory=factory)
        authz = create_authz_service(store=grants)
        scope = uuid4().hex
        await grants.bootstrap_owner("owner", scope)
        state = build_supervisor(
            session_factory=factory,
            catalog=make_catalog(),
            blob_dir=tmp_path / "blobs",
            authz=authz,
            grant_store=grants,
            workspace_store=store,
        )
        app = create_app(state=state)
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            # Percent-encoded ".." so the client does not collapse the URL.
            bad = await client.put(
                f"/v1/scopes/{scope}/files/foo/%2e%2e/secret.txt",
                content=b"x",
                headers=OWNER,
            )
            assert bad.status_code == 400


@pytest.mark.asyncio
async def test_workspace_http_uses_service_jwt_claims(tmp_path: Path) -> None:
    """Stage 3: spoofed X-Principal-Id cannot override service JWT principal."""
    from datetime import UTC, datetime, timedelta

    import jwt

    secret = "ai-service-jwt-secret-at-least-32-bytes!!"
    objects = MemoryObjectStorage()
    async with open_db(f"sqlite+aiosqlite:///{tmp_path / 'ws3.db'}") as (_, factory):
        store = create_workspace_store(
            "postgres", objects=objects, session_factory=factory
        )
        grants = create_grant_store("postgres", session_factory=factory)
        authz = create_authz_service(store=grants)
        scope = uuid4().hex
        await grants.bootstrap_owner("principal-a", scope)
        state = build_supervisor(
            session_factory=factory,
            catalog=make_catalog(),
            blob_dir=tmp_path / "blobs",
            authz=authz,
            grant_store=grants,
            workspace_store=store,
            auth_required=True,
            service_jwt_secret=secret,
        )
        app = create_app(state=state)
        now = datetime.now(UTC)
        token = jwt.encode(
            {
                "sub": "prime-agent-bff",
                "iss": "prime-agent-api",
                "aud": "yal-supervisor",
                "type": "service",
                "tenant_id": "tenant-a",
                "principal_id": "principal-a",
                "iat": now,
                "exp": now + timedelta(seconds=60),
                "jti": uuid4().hex,
            },
            secret,
            algorithm="HS256",
        )
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            ok = await client.put(
                f"/v1/scopes/{scope}/files/a.txt",
                content=b"claim-ok",
                headers={"Authorization": f"Bearer {token}"},
            )
            assert ok.status_code == 200, ok.text
            spoof = await client.put(
                f"/v1/scopes/{scope}/files/b.txt",
                content=b"nope",
                headers={
                    "Authorization": f"Bearer {token}",
                    "X-Principal-Id": "evil",
                },
            )
            assert spoof.status_code == 403
