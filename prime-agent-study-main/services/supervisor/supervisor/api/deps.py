"""FastAPI dependencies: claim-based tenant/principal and WP-M4 grant gates."""

from __future__ import annotations

import hmac
from typing import TYPE_CHECKING, Any

from fastapi import Header, HTTPException, Request
from sqlalchemy.ext.asyncio import async_sessionmaker

from supervisor.auth.service_jwt import ServiceJwtVerifier
from supervisor.journal.commands import (
    CommandConflict,
    CommandStore,
    check_same_payload,
    fingerprint,
)
from supervisor.journal.reader import JournalReader
from supervisor.seams.services import (
    AuthzService,
    GrantStore,
    Principal,
)
from supervisor.session import SessionManager

if TYPE_CHECKING:
    from supervisor.temporal.sessions import SessionWorkflows


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
    if limiter is not None and not (await limiter.check_and_record(tenant_id)).allowed:
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
        if limiter is not None and not (await limiter.check_and_record(tenant_id)).allowed:
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


def request_principal_id(request: Request, tenant_id: str) -> str:
    """The calling principal: the verified claim, else ``X-Principal-Id``, else the tenant itself."""
    claimed = _resolve_service_identity(_state(request), request.headers.get("authorization"))
    if claimed is not None:
        return claimed[1]
    return (request.headers.get("x-principal-id") or tenant_id).strip() or tenant_id


async def require_session_access(
    tenant_id: str,
    session_id: str,
    request: Request,
) -> None:
    """The session is in the caller's tenant and visible to the caller — else 404.

    Visibility is ``supervisor.session.access``: a user's sessions are their own;
    tenant-owned sessions and tenant-level callers are tenant-wide.
    """
    manager = get_manager(request)
    principal_id = request_principal_id(request, tenant_id)
    info = await manager.get(session_id, tenant_id=tenant_id, principal_id=principal_id)
    if info is None and await _still_starting(request, tenant_id, principal_id, session_id):
        info = await manager.get(session_id, tenant_id=tenant_id, principal_id=principal_id)
    if info is None:
        raise HTTPException(status_code=404, detail="session not found")


STARTING_WAIT_S = 30.0
"""How long a request for a session that is still starting waits for it to be set up."""


async def _still_starting(request: Request, tenant_id: str, principal_id: str, session_id: str) -> bool:
    """The caller's session was accepted but is not set up yet (sessions start asynchronously):
    wait until its start ends. True when it then exists (so the request can go on)."""
    import asyncio

    workflows = get_workflows(request)
    if workflows is None:
        return False
    try:
        found = await workflows.startup(session_id)
    except Exception:  # noqa: BLE001 — no workflow to wait for: an ordinary 404
        return False
    if found is None:
        return False
    startup, memo = found
    owner = str(memo.get("principal_id") or "")
    if memo.get("tenant_id") != tenant_id or (owner and principal_id not in (owner, tenant_id)):
        return False
    loop = asyncio.get_running_loop()
    deadline = loop.time() + STARTING_WAIT_S
    while startup.status == "starting" and loop.time() < deadline:
        await asyncio.sleep(0.25)
        found = await workflows.startup(session_id)
        if found is None:
            return False
        startup = found[0]
    if startup.status == "failed":
        raise HTTPException(
            status_code=409,
            detail={"reason": "session_failed_to_start", "message": startup.reason or "the session could not start"},
        )
    return startup.status == "ready"


def get_manager(request: Request) -> SessionManager:
    return _state(request).manager


def get_composition_catalog(request: Request):
    cat = getattr(_state(request), "composition_catalog", None)
    if cat is None:
        raise HTTPException(status_code=503, detail="composition catalog not configured")
    return cat


def get_session_factory(request: Request) -> async_sessionmaker:
    return _state(request).manager._db


def get_reader(request: Request) -> JournalReader:
    return _state(request).reader


def get_commands(request: Request) -> CommandStore:
    return _state(request).commands


async def recorded_command(
    request: Request, session_id: str, command_id: str, text: str
) -> dict[str, Any] | None:
    """The response already given to ``command_id``, or None if it is new.

    A command id is an idempotency key: reused with a different message it is a
    409, not a silent replay of the first message's answer (F33).
    """
    stored = await get_commands(request).get(session_id, command_id)
    if stored is not None:
        try:
            check_same_payload(command_id, stored, fingerprint(text))
        except CommandConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
    return stored


async def claim_command(
    request: Request, session_id: str, command_id: str, text: str, *, turn_id: str | None = None
) -> tuple[bool, dict[str, Any]]:
    """Claim ``command_id`` for this message: (claimed, response); 409 if it names another.
    ``turn_id`` is the run id the client gets back (the one its run's events carry)."""
    try:
        return await get_commands(request).claim(
            session_id, command_id, turn_id=turn_id, payload_fingerprint=fingerprint(text)
        )
    except CommandConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


def get_workflows(request: Request) -> SessionWorkflows | None:
    """Session workflows; None only when a test drives SessionManager in-process."""
    return getattr(_state(request), "workflows", None)


def is_draining(request: Request) -> bool:
    return bool(getattr(_state(request), "draining", False))
