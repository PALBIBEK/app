"""AuthService domain tests. Rotation/reuse family cases stay in test_auth_tokens."""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from prime_agent.db.models.refresh_token import RefreshToken
from prime_agent.services.auth_service import AuthService


@pytest.mark.asyncio
async def test_hash_and_verify_password_roundtrip():
    hashed = AuthService.hash_password("SecurePass123!")
    assert hashed != "SecurePass123!"
    assert AuthService.verify_password("SecurePass123!", hashed) is True
    assert AuthService.verify_password("wrong-password", hashed) is False


@pytest.mark.asyncio
async def test_verify_password_returns_false_on_bad_hash():
    assert AuthService.verify_password("SecurePass123!", "not-a-hash") is False


@pytest.mark.asyncio
async def test_register_user_normalizes_email_and_hashes_password(db_session):
    user = await AuthService.register_user(
        "  New.User@Example.COM ",
        "SecurePass123!",
        "New User",
        db_session,
    )
    assert user.email == "new.user@example.com"
    assert user.hashed_password != "SecurePass123!"
    assert user.role == "member"


@pytest.mark.asyncio
async def test_register_user_rejects_duplicate_email(db_session, user):
    with pytest.raises(HTTPException) as exc_info:
        await AuthService.register_user(
            user.email,
            "SecurePass123!",
            "Duplicate",
            db_session,
        )
    assert exc_info.value.status_code == 400
    assert "Unable to register" in exc_info.value.detail


@pytest.mark.asyncio
async def test_authenticate_user_success(db_session, user):
    authenticated = await AuthService.authenticate_user(
        user.email,
        "SecurePass123!",
        db_session,
    )
    assert authenticated.id == user.id


@pytest.mark.asyncio
async def test_authenticate_user_unknown_email_returns_generic_401(db_session):
    with pytest.raises(HTTPException) as exc_info:
        await AuthService.authenticate_user(
            "missing@example.com",
            "SecurePass123!",
            db_session,
        )
    assert exc_info.value.status_code == 401
    assert exc_info.value.detail == "Incorrect email or password"


@pytest.mark.asyncio
async def test_authenticate_user_wrong_password_returns_generic_401(db_session, user):
    with pytest.raises(HTTPException) as exc_info:
        await AuthService.authenticate_user(user.email, "WrongPassword12", db_session)
    assert exc_info.value.status_code == 401
    assert exc_info.value.detail == "Incorrect email or password"


@pytest.mark.asyncio
async def test_authenticate_user_inactive_returns_403(db_session, make_user):
    inactive = await make_user(email="inactive@example.com", is_active=False)
    with pytest.raises(HTTPException) as exc_info:
        await AuthService.authenticate_user(
            inactive.email,
            "SecurePass123!",
            db_session,
        )
    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_issue_tokens_persists_hashed_refresh_not_raw(db_session, user):
    tokens = await AuthService.issue_tokens(user, db_session)
    stored = await db_session.scalar(
        select(RefreshToken).where(RefreshToken.user_id == user.id)
    )
    assert stored is not None
    assert stored.token_hash != tokens["refresh_token"]
    assert stored.token_hash == AuthService.hash_refresh_token(tokens["refresh_token"])


@pytest.mark.asyncio
async def test_rotate_refresh_token_marks_previous_used(db_session, user):
    first = await AuthService.issue_tokens(user, db_session)
    second = await AuthService.rotate_refresh_token(first["refresh_token"], db_session)
    previous = await db_session.scalar(
        select(RefreshToken).where(
            RefreshToken.token_hash
            == AuthService.hash_refresh_token(first["refresh_token"])
        )
    )
    assert previous is not None
    assert previous.used_at is not None
    assert second["refresh_token"] != first["refresh_token"]


@pytest.mark.asyncio
async def test_rotate_expired_refresh_token_returns_401(db_session, user):
    tokens = await AuthService.issue_tokens(user, db_session)
    record = await db_session.scalar(
        select(RefreshToken).where(RefreshToken.user_id == user.id)
    )
    assert record is not None
    record.expires_at = datetime.now(UTC) - timedelta(minutes=1)
    await db_session.commit()

    with pytest.raises(HTTPException) as exc_info:
        await AuthService.rotate_refresh_token(tokens["refresh_token"], db_session)
    assert exc_info.value.status_code == 401


@pytest.mark.asyncio
async def test_rotate_for_inactive_user_returns_401(db_session, user):
    tokens = await AuthService.issue_tokens(user, db_session)
    user.is_active = False
    await db_session.commit()

    with pytest.raises(HTTPException) as exc_info:
        await AuthService.rotate_refresh_token(tokens["refresh_token"], db_session)
    assert exc_info.value.status_code == 401


@pytest.mark.asyncio
async def test_get_current_user_from_token_rejects_missing_user(db_session):
    token = AuthService.create_access_token("missing-user-id")
    with pytest.raises(HTTPException) as exc_info:
        await AuthService.get_current_user_from_token(token, db_session)
    assert exc_info.value.status_code == 401


@pytest.mark.asyncio
async def test_get_current_user_from_token_rejects_inactive(db_session, make_user):
    inactive = await make_user(email="inactive@example.com", is_active=False)
    token = AuthService.create_access_token(inactive.id)
    with pytest.raises(HTTPException) as exc_info:
        await AuthService.get_current_user_from_token(token, db_session)
    assert exc_info.value.status_code == 403
