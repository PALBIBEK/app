"""Who is calling: the shared service token, a session virtual key, or nobody (dev only).

A key is read from the configured ``MODEL_GATEWAY_API_KEY_HEADER`` first, then
``Authorization: Bearer``, then ``x-api-key`` — so OpenAI SDKs, Anthropic SDKs and
LiteLLM-style clients that send the key under a custom header all authenticate.
"""

from __future__ import annotations

import hmac
from dataclasses import dataclass

from fastapi import HTTPException, Request

from model_gateway.config import Settings
from model_gateway.virtual_keys import (
    VirtualKeyClaims,
    VirtualKeyStore,
    enforce_virtual_key_budget,
    verify_virtual_key,
)

_STATUS_FOR_DENIAL = {"VIRTUAL_KEY_BUDGET_EXCEEDED": 429}
"""Budget exhaustion is a quota condition (as OpenAI's ``insufficient_quota``), not a permission one."""


def gateway_error(status: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status, detail={"error": {"code": code, "message": message}})


@dataclass(frozen=True, slots=True)
class Caller:
    """An authenticated caller; ``claims`` is set only for a virtual key."""

    claims: VirtualKeyClaims | None = None

    @property
    def virtual_key(self) -> str | None:
        return self.claims.token if self.claims else None

    def may_use(self, route_id: str) -> bool:
        return self.claims is None or route_id in self.claims.allowed_models


def _settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


def _store(request: Request) -> VirtualKeyStore:
    store: VirtualKeyStore = request.app.state.virtual_keys
    return store


def _strip_bearer(value: str) -> str:
    value = value.strip()
    return value[7:].strip() if value[:7].lower() == "bearer " else value


def presented_key(request: Request) -> str | None:
    header = _settings(request).model_gateway_api_key_header
    for name in dict.fromkeys((header, "Authorization", "x-api-key")):
        value = request.headers.get(name)
        if value and (key := _strip_bearer(value)):
            return key
    return None


def _matches(raw: str, expected: str) -> bool:
    return len(raw) == len(expected) and hmac.compare_digest(raw, expected)


def require_service_token(request: Request) -> None:
    """Mint/revoke paths: the shared service token only, never a virtual key."""
    token = _settings(request).model_gateway_token
    if not token:
        raise gateway_error(503, "AUTH_UNCONFIGURED", "MODEL_GATEWAY_TOKEN required to mint virtual keys")
    got = presented_key(request)
    if got is None or not _matches(got, token):
        raise gateway_error(401, "AUTH_INVALID", "service token required")


def authenticate(request: Request, *, route_id: str | None = None) -> Caller:
    """Accept the service token or a live virtual key (allowlisted for ``route_id``, in budget).

    With no service token configured, auth is off — unless production/staging or
    ``AUTH_REQUIRED`` demand it, in which case every call fails closed with 503.
    """
    settings = _settings(request)
    service = settings.model_gateway_token
    if not service:
        if settings.auth_must_be_configured():
            raise gateway_error(503, "AUTH_UNCONFIGURED", "MODEL_GATEWAY_TOKEN required")
        return Caller()
    got = presented_key(request)
    if got is None:
        raise gateway_error(401, "AUTH_REQUIRED", "Bearer token required")
    if _matches(got, service):
        return Caller()
    if not got.startswith("vk_"):
        raise gateway_error(401, "AUTH_INVALID", "invalid bearer token")
    store = _store(request)
    try:
        claims = verify_virtual_key(store, got, route_id=route_id)
        enforce_virtual_key_budget(store, got)
    except LookupError:
        raise gateway_error(401, "AUTH_INVALID", "unknown virtual key") from None
    except PermissionError as exc:
        code = str(exc) or "VIRTUAL_KEY_DENIED"
        raise gateway_error(
            _STATUS_FOR_DENIAL.get(code, 403), code, code.lower().replace("_", " ")
        ) from None
    return Caller(claims=claims)
