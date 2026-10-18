"""Supervisor D1 service-auth matrix (T1–T3 + boot)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import jwt
import pytest
from httpx import ASGITransport, AsyncClient

from supervisor.app import build_supervisor
from supervisor.config import Settings
from supervisor.db.engine import open_db
from supervisor.main import create_app
from tests.support.session_env import make_catalog

SECRET = "ai-service-jwt-secret-at-least-32-bytes!!"


def _mint(
    *,
    tenant_id: str,
    principal_id: str,
    aud: str = "yal-supervisor",
    token_type: str = "service",
    secret: str = SECRET,
) -> str:
    now = datetime.now(UTC)
    return jwt.encode(
        {
            "sub": "prime-agent-bff",
            "iss": "prime-agent-api",
            "aud": aud,
            "type": token_type,
            "tenant_id": tenant_id,
            "principal_id": principal_id,
            "iat": now,
            "exp": now + timedelta(seconds=60),
            "jti": str(uuid4()),
        },
        secret,
        algorithm="HS256",
    )


@pytest.fixture
async def secured_api(tmp_path):
    async with open_db(f"sqlite+aiosqlite:///{tmp_path / 'auth.db'}") as (_, factory):
        state = build_supervisor(
            session_factory=factory,
            catalog=make_catalog(),
            blob_dir=tmp_path / "blobs",
            live_queue_max=32,
            auth_required=True,
            service_jwt_secret=SECRET,
        )
        app = create_app(state=state)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            yield client


@pytest.mark.asyncio
async def test_v1_sessions_without_authorization_returns_401(secured_api) -> None:
    r = await secured_api.post(
        "/v1/sessions",
        json={"harness_version": "kg-v1", "runtime": "echo"},
    )
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_valid_service_jwt_for_tenant_a_cannot_act_as_tenant_b_via_spoofed_x_tenant_id(
    secured_api,
) -> None:
    token = _mint(tenant_id="tenant-a", principal_id="tenant-a")
    r = await secured_api.post(
        "/v1/sessions",
        json={"harness_version": "kg-v1", "runtime": "echo"},
        headers={
            "Authorization": f"Bearer {token}",
            "X-Tenant-Id": "tenant-b",
        },
    )
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_user_access_jwt_as_authorization_to_supervisor_returns_401(
    secured_api,
) -> None:
    token = _mint(
        tenant_id="tenant-a",
        principal_id="tenant-a",
        aud="prime-agent-web",
        token_type="access",
    )
    r = await secured_api.post(
        "/v1/sessions",
        json={"harness_version": "kg-v1", "runtime": "echo"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_spoofed_x_principal_id_does_not_override_claim_principal(
    secured_api,
) -> None:
    token = _mint(tenant_id="tenant-a", principal_id="principal-a")
    r = await secured_api.post(
        "/v1/sessions",
        json={"harness_version": "kg-v1", "runtime": "echo"},
        headers={
            "Authorization": f"Bearer {token}",
            "X-Principal-Id": "spoofed-principal",
        },
    )
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_valid_service_jwt_creates_session_for_claim_tenant(secured_api) -> None:
    token = _mint(tenant_id="tenant-a", principal_id="principal-a")
    r = await secured_api.post(
        "/v1/sessions",
        json={"harness_version": "kg-v1", "runtime": "echo"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 201
    assert r.json()["session_id"]


@pytest.mark.asyncio
async def test_healthz_remains_public_without_authorization(secured_api) -> None:
    r = await secured_api.get("/healthz")
    assert r.status_code == 200


def test_auth_required_true_refuses_boot_without_verifier() -> None:
    settings = Settings(
        auth_required=True,
        auth_token=None,
        service_jwt_secret=None,
    )
    with pytest.raises(RuntimeError, match="AUTH_REQUIRED"):
        settings.require_auth_configured()
