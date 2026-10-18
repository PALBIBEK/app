"""Control-plane authz seams — principal → scope → grants (Track B WP-M4).

Sandbox/agent processes are untrusted for authorization. Path confinement is
defense-in-depth only. Grants live here — never inside WorkspaceStore/Hydrator.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol, runtime_checkable

from supervisor.seams.core.errors import SeamError

__all__ = [
    "AuthzService",
    "ForbiddenError",
    "Grant",
    "GrantDenied",
    "GrantStore",
    "NotScopeMember",
    "Principal",
    "ScopeNotFound",
    "UnauthorizedError",
]


class UnauthorizedError(SeamError):
    """Caller is not authenticated."""

    def __init__(self, message: str = "unauthorized") -> None:
        super().__init__(message, "AUTHZ_UNAUTHORIZED")


class ForbiddenError(SeamError):
    """Authenticated but not allowed."""

    def __init__(self, message: str, *, code: str = "AUTHZ_FORBIDDEN") -> None:
        super().__init__(message, code)


class GrantDenied(ForbiddenError):
    def __init__(
        self,
        *,
        principal_id: str,
        scope_id: str,
        grant: str,
    ) -> None:
        super().__init__(
            f"grant denied principal={principal_id!r} scope={scope_id!r} grant={grant!r}",
            code="AUTHZ_GRANT_DENIED",
        )
        self.principal_id = principal_id
        self.scope_id = scope_id
        self.grant = grant


class NotScopeMember(ForbiddenError):
    def __init__(self, *, principal_id: str, scope_id: str) -> None:
        super().__init__(
            f"not a member principal={principal_id!r} scope={scope_id!r}",
            code="AUTHZ_NOT_SCOPE_MEMBER",
        )
        self.principal_id = principal_id
        self.scope_id = scope_id


class ScopeNotFound(SeamError):
    def __init__(self, scope_id: str) -> None:
        super().__init__(f"scope not found: {scope_id!r}", "AUTHZ_SCOPE_NOT_FOUND")
        self.scope_id = scope_id


class Grant(StrEnum):
    """Frozen MVP vocabulary — ``contracts/workspace_authz_grants.md``."""

    WORKSPACE_READ = "workspace:read"
    WORKSPACE_WRITE = "workspace:write"
    SANDBOX_EXEC = "sandbox:exec"
    ARTIFACT_DOWNLOAD = "artifact:download"
    SCOPE_ADMIN = "scope:admin"


ALL_GRANTS: tuple[Grant, ...] = tuple(Grant)


@dataclass(frozen=True)
class Principal:
    """Control-plane identity. MVP: id often equals tenant header; not a sandbox claim."""

    id: str
    tenant_id: str | None = None


@runtime_checkable
class GrantStore(Protocol):
    """Persist/lookup principal ↔ scope membership + grants."""

    async def is_member(self, principal_id: str, scope_id: str) -> bool: ...

    async def has_grant(
        self, principal_id: str, scope_id: str, grant: Grant
    ) -> bool: ...

    async def list_grants(
        self, principal_id: str, scope_id: str
    ) -> frozenset[Grant]: ...

    async def add_member(self, principal_id: str, scope_id: str) -> None: ...

    async def remove_member(self, principal_id: str, scope_id: str) -> None: ...

    async def grant(
        self, principal_id: str, scope_id: str, grant: Grant
    ) -> None: ...

    async def revoke(
        self, principal_id: str, scope_id: str, grant: Grant
    ) -> None: ...

    async def bootstrap_owner(
        self, principal_id: str, scope_id: str
    ) -> None:
        """Membership + all MVP grants for scope owner."""


@runtime_checkable
class AuthzService(Protocol):
    """Policy gate in front of WorkspaceStore / Hydrator / sandbox exec."""

    async def check(
        self, principal: Principal, scope_id: str, grant: Grant
    ) -> bool: ...

    async def require(
        self, principal: Principal, scope_id: str, grant: Grant
    ) -> Principal: ...
