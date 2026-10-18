"""
Authentication routes for user registration and login.

Refresh tokens are HttpOnly cookies. CSRF uses a double-submit token echoed
in JSON so a cross-origin SPA can send X-CSRF-Token.
"""
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status

from prime_agent.config import get_settings
from prime_agent.schemas.user import (
    AccessTokenResponse,
    AuthResponse,
    UserCreate,
    UserLogin,
)
from prime_agent.services.auth_service import AuthService
from prime_agent.web.dependencies import CurrentUser, DbSession, RequireCSRF
from prime_agent.web.dependencies.csrf import (
    clear_csrf_cookie,
    generate_csrf_token,
    set_csrf_cookie,
)

router = APIRouter()


async def require_trusted_origin(request: Request) -> None:
    """Reject cross-origin browser requests outside the configured allowlist."""
    origin = request.headers.get("origin")
    if origin is not None and origin not in get_settings().cors_origins:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Untrusted request origin",
        )


TrustedOrigin = Annotated[None, Depends(require_trusted_origin)]


def _set_refresh_cookie(response: Response, token: str) -> None:
    settings = get_settings()
    response.set_cookie(
        key=settings.refresh_cookie_name,
        value=token,
        httponly=True,
        secure=settings.refresh_cookie_secure or settings.environment == "production",
        samesite="lax",
        max_age=settings.refresh_token_expire_days * 24 * 60 * 60,
        path=f"{settings.api_v1_prefix}/auth",
    )


def _clear_refresh_cookie(response: Response) -> None:
    settings = get_settings()
    response.delete_cookie(
        key=settings.refresh_cookie_name,
        path=f"{settings.api_v1_prefix}/auth",
        secure=settings.refresh_cookie_secure or settings.environment == "production",
        httponly=True,
        samesite="lax",
    )


def _apply_session(response: Response, tokens: dict[str, str]) -> dict[str, str]:
    csrf_token = generate_csrf_token()
    _set_refresh_cookie(response, tokens["refresh_token"])
    set_csrf_cookie(response, csrf_token)
    return {
        "access_token": tokens["access_token"],
        "token_type": tokens.get("token_type", "bearer"),
        "csrf_token": csrf_token,
    }


@router.post(
    "/register",
    response_model=AuthResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Register new user",
    description="Create a new user account with email and password",
)
async def register(
    user_data: UserCreate,
    db: DbSession,
    response: Response,
    _trusted_origin: TrustedOrigin,
):
    """Register a new user and issue cookie-backed refresh plus CSRF tokens."""
    user = await AuthService.register_user(
        email=user_data.email,
        password=user_data.password,
        name=user_data.name,
        db=db
    )
    tokens = await AuthService.issue_tokens(user, db)
    return {"user": user, **_apply_session(response, tokens)}


@router.post(
    "/login",
    response_model=AuthResponse,
    summary="Login user",
    description="Authenticate user with email and password",
)
async def login(
    credentials: UserLogin,
    db: DbSession,
    response: Response,
    _trusted_origin: TrustedOrigin,
):
    """Authenticate and issue a rotating refresh cookie plus CSRF token."""
    user = await AuthService.authenticate_user(
        email=credentials.email,
        password=credentials.password,
        db=db
    )
    tokens = await AuthService.issue_tokens(user, db)
    return {"user": user, **_apply_session(response, tokens)}


@router.post(
    "/refresh",
    response_model=AccessTokenResponse,
    summary="Refresh access token",
    description="Get new access token using refresh token",
)
async def refresh_token(
    request: Request,
    response: Response,
    db: DbSession,
    _trusted_origin: TrustedOrigin,
    _csrf: RequireCSRF,
):
    """Rotate the HttpOnly refresh cookie and issue a new access token."""
    settings = get_settings()
    raw_token = request.cookies.get(settings.refresh_cookie_name)
    if not raw_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid refresh token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    tokens = await AuthService.rotate_refresh_token(raw_token, db)
    return _apply_session(response, tokens)


@router.post(
    "/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Logout user",
    description="Logout current user (revoke tokens)",
)
async def logout(
    request: Request,
    response: Response,
    db: DbSession,
    current_user: CurrentUser,
    _trusted_origin: TrustedOrigin,
    _csrf: RequireCSRF,
):
    """Revoke the current refresh-token family and clear auth cookies."""
    settings = get_settings()
    raw_token = request.cookies.get(settings.refresh_cookie_name)
    if raw_token:
        await AuthService.revoke_refresh_token(raw_token, current_user.id, db)
    _clear_refresh_cookie(response)
    clear_csrf_cookie(response)
