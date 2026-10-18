"""Double-submit CSRF for cookie-authenticated auth mutations."""

import hmac
import secrets
from typing import Annotated

from fastapi import Depends, HTTPException, Request, Response, status

from prime_agent.config import get_settings


def generate_csrf_token() -> str:
    """Return a cryptographically random CSRF token."""
    return secrets.token_urlsafe(32)


def _cookie_secure() -> bool:
    settings = get_settings()
    return settings.refresh_cookie_secure or settings.environment == "production"


def _auth_cookie_path() -> str:
    return f"{get_settings().api_v1_prefix}/auth"


def set_csrf_cookie(response: Response, token: str) -> None:
    """Set a readable CSRF cookie scoped to auth routes."""
    settings = get_settings()
    response.set_cookie(
        key=settings.csrf_cookie_name,
        value=token,
        httponly=False,
        secure=_cookie_secure(),
        samesite="lax",
        max_age=settings.refresh_token_expire_days * 24 * 60 * 60,
        path=_auth_cookie_path(),
    )


def clear_csrf_cookie(response: Response) -> None:
    """Clear the CSRF cookie."""
    settings = get_settings()
    response.delete_cookie(
        key=settings.csrf_cookie_name,
        path=_auth_cookie_path(),
        secure=_cookie_secure(),
        httponly=False,
        samesite="lax",
    )


async def require_csrf(request: Request) -> None:
    """Reject cookie auth mutations unless cookie and header match."""
    settings = get_settings()
    cookie = request.cookies.get(settings.csrf_cookie_name)
    header = request.headers.get(settings.csrf_header_name)
    if (
        not cookie
        or not header
        or not hmac.compare_digest(cookie, header)
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="CSRF validation failed",
        )


RequireCSRF = Annotated[None, Depends(require_csrf)]
