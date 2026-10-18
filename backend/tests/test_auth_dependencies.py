from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
import jwt
import pytest

from prime_agent.config import get_settings
from prime_agent.main import create_app
from prime_agent.services.auth_service import AuthService
from prime_agent.web.dependencies import OptionalUser, get_db


@pytest.fixture
async def member(make_user):
    return await make_user(email="member@example.com", name="Member User")


@pytest.fixture
async def admin(make_user):
    return await make_user(email="admin@example.com", name="Admin User", role="admin")


@pytest.fixture
async def inactive(make_user):
    return await make_user(
        email="inactive@example.com",
        name="Inactive User",
        is_active=False,
    )


@pytest.fixture
async def client(db_session):
    app = create_app()

    async def override_db():
        yield db_session

    @app.get("/optional-auth")
    async def optional_auth(user: OptionalUser):
        return {"user_id": user.id if user else None}

    app.dependency_overrides[get_db] = override_db
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as api:
        yield api


def _forge_token(user_id: str, **overrides) -> str:
    settings = get_settings()
    now = datetime.now(UTC)
    payload = {
        "sub": str(user_id),
        "iat": now,
        "exp": now + timedelta(minutes=5),
        "jti": str(uuid4()),
        "iss": settings.jwt_issuer,
        "aud": settings.jwt_audience,
        "type": "access",
        **overrides,
    }
    return jwt.encode(payload, settings.secret_key, algorithm=settings.algorithm)


@pytest.mark.asyncio
async def test_valid_access_token_returns_current_user(client, member):
    token = AuthService.create_access_token(member.id)
    response = await client.get(
        "/api/v1/users/me",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    assert response.json()["email"] == member.email


@pytest.mark.asyncio
async def test_expired_access_token_returns_401(client, member):
    token = _forge_token(member.id, exp=datetime.now(UTC) - timedelta(minutes=1))
    response = await client.get(
        "/api/v1/users/me",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 401
    assert response.headers.get("www-authenticate") == "Bearer"


@pytest.mark.asyncio
async def test_refresh_typed_token_as_bearer_returns_401(client, member):
    token = _forge_token(member.id, type="refresh")
    response = await client.get(
        "/api/v1/users/me",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_wrong_audience_and_issuer_return_401(client, member):
    wrong_aud = _forge_token(member.id, aud="another-application")
    wrong_iss = _forge_token(member.id, iss="another-issuer")
    for token in (wrong_aud, wrong_iss):
        response = await client.get(
            "/api/v1/users/me",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 401


@pytest.mark.asyncio
async def test_inactive_user_returns_403(client, inactive):
    token = AuthService.create_access_token(inactive.id)
    response = await client.get(
        "/api/v1/users/me",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_missing_authorization_returns_401(client):
    response = await client.get("/api/v1/users/me")
    assert response.status_code == 401
    assert response.headers.get("www-authenticate") == "Bearer"


@pytest.mark.asyncio
async def test_member_cannot_access_admin_gate(client, member, admin):
    member_token = AuthService.create_access_token(member.id)
    denied = await client.get(
        "/api/v1/admin/ping",
        headers={"Authorization": f"Bearer {member_token}"},
    )
    assert denied.status_code == 403
    assert denied.json()["detail"] == "Insufficient permissions"

    admin_token = AuthService.create_access_token(admin.id)
    allowed = await client.get(
        "/api/v1/admin/ping",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert allowed.status_code == 200
    assert allowed.json()["role"] == "admin"


@pytest.mark.asyncio
async def test_optional_user_returns_none_for_garbage_token(client):
    missing = await client.get("/optional-auth")
    garbage = await client.get(
        "/optional-auth",
        headers={"Authorization": "Bearer not-a-jwt"},
    )
    assert missing.status_code == 200
    assert missing.json() == {"user_id": None}
    assert garbage.status_code == 200
    assert garbage.json() == {"user_id": None}
