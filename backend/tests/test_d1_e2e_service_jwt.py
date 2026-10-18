"""Cross-plane D1 E2E: system minter token is accepted by supervisor verifier."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from prime_agent.config import Settings
from prime_agent.schemas.ai_identity import TenantPrincipal
from prime_agent.security.service_jwt import ServiceJwtMinter

SECRET = "ai-service-jwt-secret-at-least-32-bytes!!"
REPO_ROOT = Path(__file__).resolve().parents[2]
SUPERVISOR_SRC = REPO_ROOT / "prime-agent-study-main" / "services" / "supervisor" / "src"
SUPERVISOR_TESTS = REPO_ROOT / "prime-agent-study-main" / "services" / "supervisor" / "tests"


@pytest.mark.asyncio
async def test_system_minted_service_jwt_accepted_by_supervisor_sessions(tmp_path):
    assert SUPERVISOR_SRC.is_dir(), f"missing supervisor src at {SUPERVISOR_SRC}"
    for path in (str(SUPERVISOR_SRC), str(SUPERVISOR_TESTS)):
        if path not in sys.path:
            sys.path.insert(0, path)

    from supervisor.app import build_supervisor
    from supervisor.db.engine import open_db
    from supervisor.main import create_app
    from support.session_env import make_catalog

    settings = Settings(
        environment="development",
        ai_service_jwt_secret=SECRET,
        ai_service_jwt_audience="yal-supervisor",
        ai_service_jwt_issuer="prime-agent-api",
        ai_service_id="prime-agent-bff",
    )
    token = ServiceJwtMinter(settings).mint(
        TenantPrincipal(tenant_id="e2e-tenant", principal_id="e2e-principal")
    )

    async with open_db(f"sqlite+aiosqlite:///{tmp_path / 'e2e.db'}") as (_, factory):
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
            denied = await client.post(
                "/v1/sessions",
                json={"harness_version": "kg-v1", "runtime": "echo"},
            )
            assert denied.status_code == 401

            ok = await client.post(
                "/v1/sessions",
                json={"harness_version": "kg-v1", "runtime": "echo"},
                headers={"Authorization": f"Bearer {token}"},
            )
            assert ok.status_code == 201, ok.text
            assert ok.json()["session_id"]
