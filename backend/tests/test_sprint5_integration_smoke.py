"""Sprint 5 FE↔BE contract smoke over ASGI (no live Postgres required).

Covers CORS credentialed origins, cookie/CSRF refresh, harness CRUD path,
and 401 without bearer — the browser contract for NEXT_PUBLIC_API_BASE_URL=/api/v1.
"""

import httpx
import pytest

from prime_agent.main import create_app
from prime_agent.web.dependencies import get_db

ORIGIN = {"Origin": "http://localhost:3000"}


@pytest.fixture
async def api_client(db_session):
    app = create_app()

    async def override_db():
        yield db_session

    app.dependency_overrides[get_db] = override_db
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


@pytest.mark.asyncio
async def test_sprint5_register_create_harness_list_and_cors(api_client):
    health = await api_client.get("/health")
    assert health.status_code == 200

    preflight = await api_client.options(
        "/api/v1/auth/login",
        headers={
            **ORIGIN,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type,authorization,x-csrf-token",
        },
    )
    assert preflight.status_code in (200, 204)
    assert preflight.headers.get("access-control-allow-origin") == "http://localhost:3000"
    assert preflight.headers.get("access-control-allow-credentials") == "true"

    register = await api_client.post(
        "/api/v1/auth/register",
        json={
            "email": "sprint5@example.com",
            "name": "Sprint Five",
            "password": "SecurePass123!",
        },
        headers=ORIGIN,
    )
    assert register.status_code == 201
    body = register.json()
    assert "refresh_token" not in body
    assert body["access_token"]
    assert body["csrf_token"]

    refresh = await api_client.post(
        "/api/v1/auth/refresh",
        headers={**ORIGIN, "X-CSRF-Token": body["csrf_token"]},
    )
    assert refresh.status_code == 200
    access = refresh.json()["access_token"]
    csrf = refresh.json()["csrf_token"]
    auth = {"Authorization": f"Bearer {access}", **ORIGIN}

    create = await api_client.post(
        "/api/v1/harnesses",
        headers=auth,
        json={
            "name": "sprint5-harness",
            "model": "gpt-4o",
            "mode": "agent",
            "description": "live smoke",
            "tools": [],
            "memory": [],
        },
    )
    assert create.status_code == 201
    harness_id = create.json()["id"]

    listed = await api_client.get("/api/v1/harnesses", headers=auth)
    assert listed.status_code == 200
    assert listed.json()["total"] == 1
    assert listed.json()["data"][0]["id"] == harness_id

    detail = await api_client.get(f"/api/v1/harnesses/{harness_id}", headers=auth)
    assert detail.status_code == 200
    assert detail.json()["name"] == "sprint5-harness"

    me = await api_client.get("/api/v1/users/me", headers=auth)
    assert me.status_code == 200
    assert me.json()["email"] == "sprint5@example.com"

    assert (await api_client.get("/api/v1/harnesses")).status_code == 401

    logout = await api_client.post(
        "/api/v1/auth/logout",
        headers={**auth, "X-CSRF-Token": csrf},
    )
    assert logout.status_code == 204
