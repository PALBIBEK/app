"""HTTP service auth for Memory Service (D1 / D7 production).

Preferred: service JWT with tenant_id claim (SERVICE_JWT_SECRET / MEMORY_SERVICE_JWT_SECRET).
When Bearer is a verified JWT, the tenant_id claim always wins; a mismatched
X-Tenant-Id is ignored (claim is source of truth) or rejected with 403 depending
on MEMORY_JWT_STRICT_HEADER (default: claim wins / ignore mismatch).

Static MEMORY_SERVICE_TOKEN + X-Tenant-Id is CI/local residual only when
MEMORY_ALLOW_STATIC_TOKEN=1 (see contracts/service_auth.md).
Fail closed when AUTH_REQUIRED / staging|production and neither is configured.
"""

from __future__ import annotations

import hmac
import os
from typing import Any

import jwt
from fastapi import HTTPException, Request
from jwt.exceptions import PyJWTError


def memory_service_token() -> str:
    return os.environ.get("MEMORY_SERVICE_TOKEN", "").strip()


def memory_jwt_secret() -> str:
    return (
        os.environ.get("MEMORY_SERVICE_JWT_SECRET", "").strip()
        or os.environ.get("SERVICE_JWT_SECRET", "").strip()
    )


def memory_allow_static_token() -> bool:
    return os.environ.get("MEMORY_ALLOW_STATIC_TOKEN", "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def memory_jwt_audiences() -> list[str]:
    raw = os.environ.get(
        "MEMORY_JWT_AUDIENCE",
        "yal-memory,yal-supervisor",
    ).strip()
    parts = [a.strip() for a in raw.split(",") if a.strip()]
    return parts or ["yal-memory"]


def memory_jwt_issuer() -> str:
    return os.environ.get("MEMORY_JWT_ISSUER", "prime-agent-api").strip() or "prime-agent-api"


def auth_must_be_configured() -> bool:
    if os.environ.get("AUTH_REQUIRED", "").lower() in ("1", "true", "yes"):
        return True
    env = (
        os.environ.get("ENV", os.environ.get("ENVIRONMENT", "")).strip().lower()
    )
    return env in ("production", "staging")


def _extract_bearer(authorization: str | None) -> str | None:
    if not authorization:
        return None
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        return None
    return token.strip()


def _bearer_matches(raw: str, expected: str) -> bool:
    if len(raw) != len(expected):
        return False
    return hmac.compare_digest(raw, expected)


def _verify_service_jwt(raw: str) -> str:
    """Return tenant_id from a verified service JWT."""
    secret = memory_jwt_secret()
    if not secret:
        raise ValueError("jwt unconfigured")
    try:
        payload: dict[str, Any] = jwt.decode(
            raw,
            secret,
            algorithms=["HS256"],
            audience=memory_jwt_audiences(),
            issuer=memory_jwt_issuer(),
            leeway=0,
            options={
                "require": [
                    "sub",
                    "exp",
                    "iat",
                    "jti",
                    "iss",
                    "aud",
                    "type",
                    "tenant_id",
                    "principal_id",
                ],
            },
        )
    except PyJWTError as exc:
        raise ValueError("invalid service token") from exc

    if payload.get("type") != "service":
        raise ValueError("invalid service token")
    tenant_id = str(payload.get("tenant_id") or "").strip()
    if not tenant_id:
        raise ValueError("invalid service token")
    return tenant_id


def caller_tenant(request: Request) -> str | None:
    return getattr(request.state, "caller_tenant", None)


def enforce_scope_tenant(request: Request, scope_tenant: str) -> None:
    """Body/query scope tenant must match verified caller tenant when auth is on."""
    claimed = caller_tenant(request)
    if claimed is not None and claimed != scope_tenant.strip():
        raise HTTPException(
            status_code=403,
            detail={"code": "TENANT_MISMATCH", "message": "scope tenant mismatch"},
        )


def enforce_item_tenant(request: Request, item_tenant: str) -> None:
    """Cross-tenant get → 404 (no existence leak)."""
    claimed = caller_tenant(request)
    if claimed is not None and claimed != item_tenant.strip():
        raise HTTPException(
            status_code=404,
            detail={"code": "ITEM_NOT_FOUND", "message": "not found"},
        )


def resolve_http_auth(request: Request) -> None:
    """Validate Bearer and bind verified tenant onto request.state.caller_tenant.

    When auth is off (dev), caller_tenant stays None.
    """
    jwt_secret = memory_jwt_secret()
    shared = memory_service_token()
    static_ok = bool(shared) and memory_allow_static_token()
    if not jwt_secret and not static_ok:
        if auth_must_be_configured():
            raise HTTPException(
                status_code=503,
                detail={
                    "code": "AUTH_UNCONFIGURED",
                    "message": (
                        "SERVICE_JWT_SECRET required "
                        "(or MEMORY_SERVICE_TOKEN with MEMORY_ALLOW_STATIC_TOKEN=1)"
                    ),
                },
            )
        request.state.caller_tenant = None
        return

    raw = _extract_bearer(request.headers.get("authorization"))
    if raw is None:
        raise HTTPException(
            status_code=401,
            detail={"code": "AUTH_REQUIRED", "message": "Bearer token required"},
        )

    if jwt_secret:
        try:
            tenant = _verify_service_jwt(raw)
            # Claim wins: X-Tenant-Id cannot override JWT tenant_id.
            # Default ignore mismatch; set MEMORY_JWT_STRICT_HEADER=1 to 403.
            hdr = (request.headers.get("x-tenant-id") or "").strip()
            strict = os.environ.get("MEMORY_JWT_STRICT_HEADER", "").strip().lower() in (
                "1",
                "true",
                "yes",
                "on",
            )
            if strict and hdr and hdr != tenant:
                raise HTTPException(
                    status_code=403,
                    detail={"code": "TENANT_MISMATCH", "message": "tenant mismatch"},
                )
            request.state.caller_tenant = tenant
            return
        except HTTPException:
            raise
        except ValueError:
            if not static_ok:
                raise HTTPException(
                    status_code=401,
                    detail={"code": "AUTH_INVALID", "message": "invalid bearer token"},
                ) from None

    if static_ok:
        if not _bearer_matches(raw, shared):
            raise HTTPException(
                status_code=401,
                detail={"code": "AUTH_INVALID", "message": "invalid bearer token"},
            )
        tenant = (request.headers.get("x-tenant-id") or "").strip()
        if not tenant:
            raise HTTPException(
                status_code=401,
                detail={"code": "TENANT_REQUIRED", "message": "X-Tenant-Id required"},
            )
        request.state.caller_tenant = tenant
        return

    raise HTTPException(
        status_code=401,
        detail={"code": "AUTH_INVALID", "message": "invalid bearer token"},
    )
