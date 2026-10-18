"""Sync JWT claim policy for access tokens.

Encode/decode is CPU-bound crypto, not I/O. Callers remain async only for
database work. Refresh tokens stay opaque and are not JWTs.
"""

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import jwt
from fastapi import HTTPException, status
from jwt.exceptions import PyJWTError

from prime_agent.config import get_settings


class JWTManager:
    """Create and validate short-lived access JWTs."""

    @staticmethod
    def create_access_token(
        user_id: str,
        expires_delta: timedelta | None = None,
    ) -> str:
        """Encode an access token with pinned issuer, audience, and type."""
        settings = get_settings()
        if expires_delta is None:
            expires_delta = timedelta(minutes=settings.access_token_expire_minutes)

        now = datetime.now(UTC)
        payload = {
            "sub": str(user_id),
            "exp": now + expires_delta,
            "iat": now,
            "jti": str(uuid4()),
            "iss": settings.jwt_issuer,
            "aud": settings.jwt_audience,
            "type": "access",
        }
        return jwt.encode(
            payload,
            settings.secret_key,
            algorithm=settings.algorithm,
        )

    @staticmethod
    def decode_access_token(token: str) -> dict[str, Any]:
        """Validate signature and required claims. Rejects non-access tokens."""
        settings = get_settings()
        try:
            payload = jwt.decode(
                token,
                settings.secret_key,
                algorithms=[settings.algorithm],
                audience=settings.jwt_audience,
                issuer=settings.jwt_issuer,
                leeway=0,
                options={
                    "require": ["sub", "exp", "iat", "jti", "iss", "aud"],
                },
            )
        except PyJWTError:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Could not validate credentials",
                headers={"WWW-Authenticate": "Bearer"},
            ) from None

        if payload.get("type") != "access":
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Could not validate credentials",
                headers={"WWW-Authenticate": "Bearer"},
            )
        return payload
