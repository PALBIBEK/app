"""Authenticated self-service user routes."""

from fastapi import APIRouter, Response, status

from prime_agent.config import get_settings
from prime_agent.schemas.user import (
    ChangePasswordRequest,
    UserResponse,
    UserUpdate,
)
from prime_agent.services.user_service import UserService
from prime_agent.web.dependencies import CurrentUser, DbSession
from prime_agent.web.dependencies.csrf import clear_csrf_cookie

router = APIRouter()


@router.get("/me", response_model=UserResponse)
async def get_profile(current_user: CurrentUser) -> UserResponse:
    """Get the authenticated user's profile."""
    return current_user


@router.patch("/me", response_model=UserResponse)
async def update_profile(
    profile_data: UserUpdate,
    db: DbSession,
    current_user: CurrentUser,
) -> UserResponse:
    """Update the authenticated user's non-privileged profile fields."""
    return await UserService.update_profile(
        current_user,
        profile_data.model_dump(exclude_unset=True),
        db,
    )


@router.post(
    "/me/password",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
async def change_password(
    password_data: ChangePasswordRequest,
    response: Response,
    db: DbSession,
    current_user: CurrentUser,
) -> None:
    """Change the current password and revoke all refresh sessions."""
    await UserService.change_password(
        current_user,
        password_data.current_password,
        password_data.new_password,
        db,
    )
    settings = get_settings()
    response.delete_cookie(
        key=settings.refresh_cookie_name,
        path=f"{settings.api_v1_prefix}/auth",
        secure=settings.refresh_cookie_secure or settings.environment == "production",
        httponly=True,
        samesite="lax",
    )
    clear_csrf_cookie(response)
