import httpx
import pytest
from sqlalchemy import select

from prime_agent.db.models.refresh_token import RefreshToken
from prime_agent.main import create_app
from prime_agent.services.auth_service import AuthService
from prime_agent.web.dependencies import get_current_user, get_db


@pytest.fixture
async def client(db_session, user):
    app = create_app()

    async def override_db():
        yield db_session

    async def override_current_user():
        return user

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = override_current_user
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as api:
        yield api


@pytest.mark.asyncio
async def test_user_can_read_and_update_only_profile_fields(client):
    profile = await client.get("/api/v1/users/me")
    assert profile.status_code == 200
    assert profile.json()["role"] == "member"

    update_response = await client.patch(
        "/api/v1/users/me",
        json={"name": "Updated Name"},
    )
    assert update_response.status_code == 200
    assert update_response.json()["name"] == "Updated Name"

    privilege_attempt = await client.patch(
        "/api/v1/users/me",
        json={"role": "owner", "is_active": False},
    )
    assert privilege_attempt.status_code == 422


@pytest.mark.asyncio
async def test_password_change_revokes_refresh_sessions(client, db_session, user):
    token_pair = await AuthService.issue_tokens(user, db_session)

    response = await client.post(
        "/api/v1/users/me/password",
        json={
            "current_password": "SecurePass123!",
            "new_password": "DifferentPass456!",
        },
    )
    assert response.status_code == 204
    assert "max-age=0" in response.headers["set-cookie"].lower()
    assert AuthService.verify_password("DifferentPass456!", user.hashed_password)

    refresh_record = await db_session.scalar(
        select(RefreshToken).where(
            RefreshToken.token_hash
            == AuthService.hash_refresh_token(token_pair["refresh_token"])
        )
    )
    assert refresh_record is not None
    assert refresh_record.revoked_at is not None
