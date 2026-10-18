"""Cookie CSRF and login transport tests."""

import httpx
import pytest

from prime_agent.main import create_app
from prime_agent.web.dependencies import get_db


@pytest.fixture
async def client(db_session):
    app = create_app()

    async def override_db():
        yield db_session

    app.dependency_overrides[get_db] = override_db
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as api:
        yield api


async def _register(client: httpx.AsyncClient) -> httpx.Response:
    return await client.post(
        "/api/v1/auth/register",
        json={
            "email": "browser@example.com",
            "name": "Browser User",
            "password": "SecurePass123!",
        },
        headers={"Origin": "http://localhost:3000"},
    )


@pytest.mark.asyncio
async def test_login_sets_csrf_cookie_and_returns_csrf_token(client):
    await _register(client)
    login = await client.post(
        "/api/v1/auth/login",
        json={
            "email": "browser@example.com",
            "password": "SecurePass123!",
        },
        headers={"Origin": "http://localhost:3000"},
    )
    assert login.status_code == 200
    assert login.json()["csrf_token"]
    set_cookie = login.headers.get_list("set-cookie")
    assert any("prime_agent_csrf=" in value for value in set_cookie)
    assert any(
        "prime_agent_refresh=" in value and "httponly" in value.lower()
        for value in set_cookie
    )


@pytest.mark.asyncio
async def test_login_authenticates_and_omits_refresh_from_json(client):
    await _register(client)
    login = await client.post(
        "/api/v1/auth/login",
        json={
            "email": "browser@example.com",
            "password": "SecurePass123!",
        },
        headers={"Origin": "http://localhost:3000"},
    )
    assert login.status_code == 200
    body = login.json()
    assert body["access_token"]
    assert body["user"]["email"] == "browser@example.com"
    assert "refresh_token" not in body


@pytest.mark.asyncio
async def test_refresh_rejects_mismatched_csrf_header(client):
    await _register(client)
    refresh = await client.post(
        "/api/v1/auth/refresh",
        headers={
            "Origin": "http://localhost:3000",
            "X-CSRF-Token": "not-the-cookie-value",
        },
    )
    assert refresh.status_code == 403
    assert refresh.json()["detail"] == "CSRF validation failed"


@pytest.mark.asyncio
async def test_refresh_rejects_missing_csrf_cookie(client):
    register = await _register(client)
    csrf_token = register.json()["csrf_token"]
    client.cookies.clear()
    refresh = await client.post(
        "/api/v1/auth/refresh",
        headers={
            "Origin": "http://localhost:3000",
            "X-CSRF-Token": csrf_token,
        },
    )
    assert refresh.status_code == 403
    assert refresh.json()["detail"] == "CSRF validation failed"


@pytest.mark.asyncio
async def test_logout_rejects_missing_csrf(client):
    register = await _register(client)
    logout = await client.post(
        "/api/v1/auth/logout",
        headers={
            "Authorization": f"Bearer {register.json()['access_token']}",
            "Origin": "http://localhost:3000",
        },
    )
    assert logout.status_code == 403
    assert logout.json()["detail"] == "CSRF validation failed"
