from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
import jwt
import pytest
from fastapi import HTTPException
from sqlalchemy import select

from prime_agent.config import get_settings
from prime_agent.db.models.refresh_token import RefreshToken
from prime_agent.main import create_app
from prime_agent.services.auth_service import AuthService
from prime_agent.web.dependencies import get_db


@pytest.mark.asyncio
async def test_refresh_tokens_are_opaque_hashed_and_rotated(db_session, user):
    first_pair = await AuthService.issue_tokens(user, db_session)
    first_raw = first_pair["refresh_token"]

    first_record = await db_session.scalar(
        select(RefreshToken).where(
            RefreshToken.token_hash == AuthService.hash_refresh_token(first_raw)
        )
    )
    assert first_record is not None
    assert first_record.token_hash != first_raw

    second_pair = await AuthService.rotate_refresh_token(first_raw, db_session)
    second_raw = second_pair["refresh_token"]
    await db_session.refresh(first_record)

    assert second_raw != first_raw
    assert first_record.used_at is not None
    assert second_pair["access_token"]


@pytest.mark.asyncio
async def test_reusing_rotated_token_revokes_its_family(db_session, user):
    first_pair = await AuthService.issue_tokens(user, db_session)
    second_pair = await AuthService.rotate_refresh_token(
        first_pair["refresh_token"],
        db_session,
    )

    with pytest.raises(HTTPException) as exc_info:
        await AuthService.rotate_refresh_token(first_pair["refresh_token"], db_session)

    assert exc_info.value.status_code == 401
    second_record = await db_session.scalar(
        select(RefreshToken).where(
            RefreshToken.token_hash
            == AuthService.hash_refresh_token(second_pair["refresh_token"])
        )
    )
    assert second_record is not None
    assert second_record.revoked_at is not None


@pytest.mark.asyncio
async def test_logout_revokes_current_users_refresh_family(db_session, user):
    first_tokens = await AuthService.issue_tokens(user, db_session)
    tokens = await AuthService.rotate_refresh_token(
        first_tokens["refresh_token"],
        db_session,
    )

    await AuthService.revoke_refresh_token(
        tokens["refresh_token"],
        user.id,
        db_session,
    )

    records = list(
        (
            await db_session.scalars(
                select(RefreshToken).where(RefreshToken.user_id == user.id)
            )
        ).all()
    )
    assert len(records) == 2
    assert all(record.revoked_at is not None for record in records)


@pytest.mark.asyncio
async def test_register_refresh_and_logout_use_http_only_rotating_cookie(db_session):
    app = create_app()

    async def override_db():
        yield db_session

    app.dependency_overrides[get_db] = override_db
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        register = await client.post(
            "/api/v1/auth/register",
            json={
                "email": "browser@example.com",
                "name": "Browser User",
                "password": "SecurePass123!",
            },
            headers={"Origin": "http://localhost:3000"},
        )
        assert register.status_code == 201
        assert "refresh_token" not in register.json()
        assert "httponly" in register.headers["set-cookie"].lower()
        access_token = register.json()["access_token"]
        csrf_token = register.json()["csrf_token"]

        refresh_without_csrf = await client.post(
            "/api/v1/auth/refresh",
            headers={"Origin": "http://localhost:3000"},
        )
        assert refresh_without_csrf.status_code == 403

        refresh = await client.post(
            "/api/v1/auth/refresh",
            headers={
                "Origin": "http://localhost:3000",
                "X-CSRF-Token": csrf_token,
            },
        )
        assert refresh.status_code == 200
        assert set(refresh.json()) == {"access_token", "token_type", "csrf_token"}
        csrf_token = refresh.json()["csrf_token"]

        logout = await client.post(
            "/api/v1/auth/logout",
            headers={
                "Authorization": f"Bearer {access_token}",
                "Origin": "http://localhost:3000",
                "X-CSRF-Token": csrf_token,
            },
        )
        assert logout.status_code == 204
        assert "max-age=0" in logout.headers["set-cookie"].lower()


def test_access_token_rejects_wrong_audience():
    settings = get_settings()
    now = datetime.now(UTC)
    token = jwt.encode(
        {
            "sub": str(uuid4()),
            "iat": now,
            "exp": now + timedelta(minutes=5),
            "jti": str(uuid4()),
            "iss": settings.jwt_issuer,
            "aud": "another-application",
            "type": "access",
        },
        settings.secret_key,
        algorithm=settings.algorithm,
    )

    with pytest.raises(HTTPException) as exc_info:
        AuthService.decode_token(token)

    assert exc_info.value.status_code == 401
