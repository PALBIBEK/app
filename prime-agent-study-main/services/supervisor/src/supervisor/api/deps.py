"""FastAPI dependencies: claim-based tenant/principal and WP-M4 grant gates."""

from __future__ import annotations

import hmac
from collections.abc import Callable, Coroutine
from typing import Any

from fastapi import Header, HTTPException, Request
from sqlalchemy.ext.asyncio import async_sessionmaker

from supervisor.auth.service_jwt import ServiceJwtVerifier
from supervisor.journal.commands import CommandStore
from supervisor.journal.reader import JournalReader
from supervisor.seams.services import (
    AuthzService,
    ForbiddenError,
    Grant,
    GrantDenied,
    GrantStore,
    NotScopeMember,
    Principal,
    UnauthorizedError,
)
from supervisor.session import SessionManager


def _state(request: Request):
    state = getattr(request.app.state, "supervisor", None)
    if state is None:
        raise HTTPException(status_code=503, detail="supervisor not configured")
    return state


def _extract_bearer(authorization: str | None) -> str | None:
    if not authorization:
        return None
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        return None
    return token.strip()


def _bearer_matches(raw: str, expected: str) -> bool:
    """Constant-time compare that never raises on length mismatch."""
    if len(raw) != len(expected):
        return False
    return hmac.compare_digest(raw, expected)


def _resolve_service_identity(
    state,
    authorization: str | None,
) -> tuple[str, str] | None:
    """Return (tenant_id, principal_id) from service JWT or legacy AUTH_TOKEN.

    Returns None when auth is intentionally off (dev/unit fixtures).
    """
    auth_required = bool(getattr(state, "auth_required", False))
    jwt_secret = getattr(state, "service_jwt_secret", None)
    legacy_token = getattr(state, "auth_token", None)
    auth_enabled = auth_required or bool(jwt_secret) or bool(legacy_token)

    if not auth_enabled:
        return None

    raw = _extract_bearer(authorization)
    if raw is None:
        raise HTTPException(status_code=401, detail="unauthorized")

    if jwt_secret:
        try:
            identity = ServiceJwtVerifier(
                secret=jwt_secret,
                audience=getattr(state, "service_jwt_audience", "yal-supervisor"),
                issuer=getattr(state, "service_jwt_issuer", "prime-agent-api"),
            ).verify(raw)
        except ValueError as exc:
            if legacy_token is None or not _bearer_matches(raw, legacy_token):
                raise HTTPException(status_code=401, detail="unauthorized") from exc
            return None
        return identity.tenant_id, identity.principal_id

    assert legacy_token is not None
    if not _bearer_matches(raw, legacy_token):
        raise HTTPException(status_code=401, detail="unauthorized")
    return None


async def current_tenant(
    request: Request,
    authorization: str | None = Header(default=None),
    x_tenant_id: str | None = Header(default=None),
) -> str:
    """Resolve tenant from verified service claims when auth is enabled."""
    state = _state(request)
    claimed = _resolve_service_identity(state, authorization)

    if claimed is not None:
        tenant_id, _principal_id = claimed
        if x_tenant_id and x_tenant_id.strip() != tenant_id:
            raise HTTPException(status_code=403, detail="tenant mismatch")
    else:
        # Auth off or legacy static token: require header (existing test contract).
        if not x_tenant_id:
            raise HTTPException(status_code=401, detail="missing tenant")
        tenant_id = x_tenant_id.strip()

    limiter = getattr(state, "rate_limiter", None)
    if limiter is not None and not limiter.allow(tenant_id):
        raise HTTPException(status_code=429, detail="tenant rate limit exceeded")
    metrics = getattr(state, "metrics", None)
    if metrics is not None:
        metrics.record_request(tenant_id)
    return tenant_id


async def current_principal(
    request: Request,
    authorization: str | None = Header(default=None),
    x_tenant_id: str | None = Header(default=None),
    x_principal_id: str | None = Header(default=None),
) -> Principal:
    """Resolve Principal from verified claims when auth is enabled.

    Residual (WP-M7): when auth is off / static bearer, ``X-Principal-Id`` is
    client-supplied — never trust sandbox/agent identity; IdP binding is later.
    """
    state = _state(request)
    claimed = _resolve_service_identity(state, authorization)

    if claimed is not None:
        tenant_id, principal_id = claimed
        if x_tenant_id and x_tenant_id.strip() != tenant_id:
            raise HTTPException(status_code=403, detail="tenant mismatch")
        if x_principal_id and x_principal_id.strip() != principal_id:
            raise HTTPException(status_code=403, detail="principal mismatch")
        limiter = getattr(state, "rate_limiter", None)
        if limiter is not None and not limiter.allow(tenant_id):
            raise HTTPException(status_code=429, detail="tenant rate limit exceeded")
        metrics = getattr(state, "metrics", None)
        if metrics is not None:
            metrics.record_request(tenant_id)
    else:
        tenant_id = await current_tenant(
            request, authorization=authorization, x_tenant_id=x_tenant_id
        )
        principal_id = (x_principal_id or tenant_id).strip()
        if not principal_id:
            raise HTTPException(status_code=401, detail="missing principal")

    return Principal(id=principal_id, tenant_id=tenant_id)


def get_authz(request: Request) -> AuthzService:
    authz = getattr(_state(request), "authz", None)
    if authz is None:
        raise HTTPException(status_code=503, detail="authz not configured")
    return authz


def get_grant_store(request: Request) -> GrantStore:
    store = getattr(_state(request), "grant_store", None)
    if store is None:
        raise HTTPException(status_code=503, detail="grant store not configured")
    return store  # type: ignore[return-value]


def require_grant(
    grant: Grant | str,
) -> Callable[..., Coroutine[Any, Any, Principal]]:
    """Normative FastAPI dep: ``require_grant(scope_id, grant) -> Principal``."""
    needed = Grant(grant) if not isinstance(grant, Grant) else grant

    async def _dep(
        scope_id: str,
        request: Request,
        principal: Principal | None = None,
        authorization: str | None = Header(default=None),
        x_tenant_id: str | None = Header(default=None),
        x_principal_id: str | None = Header(default=None),
    ) -> Principal:
        resolved = principal or await current_principal(
            request,
            authorization=authorization,
            x_tenant_id=x_tenant_id,
            x_principal_id=x_principal_id,
        )
        authz = get_authz(request)
        try:
            return await authz.require(resolved, scope_id, needed)
        except UnauthorizedError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc
        except (GrantDenied, NotScopeMember, ForbiddenError) as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc

    return _dep


async def require_session_access(
    tenant_id: str,
    session_id: str,
    request: Request,
) -> None:
    """Every session query filters by tenant — raise 404 on miss or mismatch."""
    manager = get_manager(request)
    info = await manager.get(session_id, tenant_id=tenant_id)
    if info is None:
        raise HTTPException(status_code=404, detail="session not found")


def get_manager(request: Request) -> SessionManager:
    return _state(request).manager


def get_composition_catalog(request: Request):
    cat = getattr(_state(request), "composition_catalog", None)
    if cat is None:
        raise HTTPException(status_code=503, detail="composition catalog not configured")
    return cat


def get_session_factory(request: Request) -> async_sessionmaker:
    return _state(request).manager._db  # noqa: SLF001


def get_reader(request: Request) -> JournalReader:
    return _state(request).reader


def get_commands(request: Request) -> CommandStore:
    return _state(request).commands


def is_draining(request: Request) -> bool:
    return bool(getattr(_state(request), "draining", False))
