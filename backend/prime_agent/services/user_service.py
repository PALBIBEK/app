"""User profile and password management."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from prime_agent.db.models.refresh_token import RefreshToken
from prime_agent.db.models.user import User
from prime_agent.services.auth_service import AuthService

_ALLOWED_PROFILE_FIELDS = frozenset({"email", "name", "avatar"})


class UserService:
    """Self-service profile operations for the authenticated user."""

    @classmethod
    async def update_profile(
        cls,
        user: User,
        data: dict[str, Any],
        db: AsyncSession,
    ) -> User:
        unexpected = set(data) - _ALLOWED_PROFILE_FIELDS
        if unexpected:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Unexpected fields: {', '.join(sorted(unexpected))}",
            )

        if "email" in data and data["email"] is not None:
            normalized = str(data["email"]).strip().lower()
            stmt = select(User).where(User.email == normalized, User.id != user.id)
            conflict = (await db.execute(stmt)).scalar_one_or_none()
            if conflict is not None:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="Unable to update profile",
                )
            user.email = normalized

        if "name" in data and data["name"] is not None:
            user.name = data["name"]
        if "avatar" in data:
            user.avatar = data["avatar"]

        await db.commit()
        await db.refresh(user)
        return user

    @classmethod
    async def change_password(
        cls,
        user: User,
        current_password: str,
        new_password: str,
        db: AsyncSession,
    ) -> None:
        if not AuthService.verify_password(current_password, user.hashed_password):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Current password is incorrect",
            )
        if current_password == new_password:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="New password must differ from the current password",
            )

        user.hashed_password = AuthService.hash_password(new_password)
        await db.execute(
            update(RefreshToken)
            .where(
                RefreshToken.user_id == user.id,
                RefreshToken.revoked_at.is_(None),
            )
            .values(revoked_at=datetime.now(UTC))
        )
        await db.commit()
