"""UserService profile and password tests."""

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from prime_agent.db.models.refresh_token import RefreshToken
from prime_agent.services.auth_service import AuthService
from prime_agent.services.user_service import UserService


@pytest.mark.asyncio
async def test_update_profile_updates_allowed_fields(db_session, user):
    updated = await UserService.update_profile(user, {"name": "New Name"}, db_session)
    assert updated.name == "New Name"


@pytest.mark.asyncio
async def test_update_profile_rejects_unexpected_fields(db_session, user):
    with pytest.raises(HTTPException) as exc_info:
        await UserService.update_profile(user, {"role": "admin"}, db_session)
    assert exc_info.value.status_code == 422


@pytest.mark.asyncio
async def test_update_profile_rejects_conflicting_email_with_409(db_session, user, make_user):
    await make_user(email="taken@example.com")
    with pytest.raises(HTTPException) as exc_info:
        await UserService.update_profile(
            user,
            {"email": "taken@example.com"},
            db_session,
        )
    assert exc_info.value.status_code == 409
    assert "Unable to update profile" in exc_info.value.detail


@pytest.mark.asyncio
async def test_update_profile_normalizes_email(db_session, user):
    updated = await UserService.update_profile(
        user,
        {"email": "  New.Email@Example.COM "},
        db_session,
    )
    assert updated.email == "new.email@example.com"


@pytest.mark.asyncio
async def test_change_password_rejects_wrong_current_password(db_session, user):
    with pytest.raises(HTTPException) as exc_info:
        await UserService.change_password(
            user,
            "WrongPassword12",
            "DifferentPass456!",
            db_session,
        )
    assert exc_info.value.status_code == 400


@pytest.mark.asyncio
async def test_change_password_rejects_unchanged_password(db_session, user):
    with pytest.raises(HTTPException) as exc_info:
        await UserService.change_password(
            user,
            "SecurePass123!",
            "SecurePass123!",
            db_session,
        )
    assert exc_info.value.status_code == 400


@pytest.mark.asyncio
async def test_change_password_revokes_all_active_refresh_tokens(db_session, user):
    tokens = await AuthService.issue_tokens(user, db_session)
    await UserService.change_password(
        user,
        "SecurePass123!",
        "DifferentPass456!",
        db_session,
    )
    record = await db_session.scalar(
        select(RefreshToken).where(
            RefreshToken.token_hash
            == AuthService.hash_refresh_token(tokens["refresh_token"])
        )
    )
    assert record is not None
    assert record.revoked_at is not None
    assert AuthService.verify_password("DifferentPass456!", user.hashed_password)
