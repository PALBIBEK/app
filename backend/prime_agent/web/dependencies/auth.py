"""Authentication and authorization FastAPI dependencies.

JWT claim policy lives in jwt_manager. This module only performs DI:
extract the bearer token, load the principal, and apply coarse RBAC.
Object ownership stays in the service layer.
"""

from collections.abc import Callable
from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from prime_agent.db.models.user import User
from prime_agent.services.auth_service import AuthService
from prime_agent.web.dependencies.database import get_db

security = HTTPBearer(auto_error=False)


async def get_current_user(
    credentials: Annotated[
        HTTPAuthorizationCredentials | None,
        Depends(security),
    ],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> User:
    """Load the authenticated user from a valid access JWT."""
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return await AuthService.get_current_user_from_token(credentials.credentials, db)


def require_role(required_role: str) -> Callable:
    """Factory that requires an exact role from the database user record."""

    async def role_checker(
        current_user: Annotated[User, Depends(get_current_user)],
    ) -> User:
        if current_user.role != required_role:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Insufficient permissions",
            )
        return current_user

    return role_checker


def require_any_role(*allowed_roles: str) -> Callable:
    """Factory that requires the user to hold one of the allowed roles."""

    async def role_checker(
        current_user: Annotated[User, Depends(get_current_user)],
    ) -> User:
        if current_user.role not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Insufficient permissions",
            )
        return current_user

    return role_checker


async def get_optional_user(
    credentials: Annotated[
        HTTPAuthorizationCredentials | None,
        Depends(security),
    ],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> User | None:
    """Return the current user when a valid token is present, else None."""
    if credentials is None:
        return None

    try:
        return await AuthService.get_current_user_from_token(
            credentials.credentials,
            db,
        )
    except HTTPException:
        return None

CurrentUser = Annotated[User, Depends(get_current_user)]
OptionalUser = Annotated[User | None, Depends(get_optional_user)]
AdminUser = Annotated[User, Depends(require_role("admin"))]
