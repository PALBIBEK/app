"""FastAPI dependencies for Prime Agent."""

from prime_agent.web.dependencies.auth import (
    AdminUser,
    CurrentUser,
    OptionalUser,
    get_current_user,
    get_optional_user,
    require_any_role,
    require_role,
)
from prime_agent.web.dependencies.csrf import RequireCSRF, require_csrf
from prime_agent.web.dependencies.database import DbSession, get_db

__all__ = [
    "AdminUser",
    "CurrentUser",
    "DbSession",
    "OptionalUser",
    "RequireCSRF",
    "get_current_user",
    "get_db",
    "get_optional_user",
    "require_any_role",
    "require_csrf",
    "require_role",
]
