"""Session-scoped virtual keys — provider secrets stay in the gateway process.

WP-M5: mint with the shared service token; chat accepts ``vk_…`` tokens.
Memory store is the MVP; Postgres can replace without changing callers.
"""

from __future__ import annotations

import secrets
import time
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class VirtualKeyClaims:
    key_id: str
    token: str
    tenant_id: str
    session_id: str
    principal_id: str
    allowed_models: tuple[str, ...]
    max_budget_usd: float
    expires_at: float
    revoked: bool = False
    scope_id: str | None = None


@dataclass
class MintRequest:
    tenant_id: str
    session_id: str
    principal_id: str
    allowed_models: tuple[str, ...] = ()
    ttl_s: int = 3600
    max_budget_usd: float = 5.0
    scope_id: str | None = None


@runtime_checkable
class VirtualKeyStore(Protocol):
    def mint(self, req: MintRequest) -> VirtualKeyClaims: ...

    def get(self, token: str) -> VirtualKeyClaims | None: ...

    def revoke(self, token: str) -> None: ...

    def revoke_session(self, session_id: str) -> int: ...

    def add_spend(self, token: str, amount_usd: float) -> float: ...

    def spent_usd(self, token: str) -> float: ...


@dataclass
class MemoryVirtualKeyStore:
    """In-process virtual key registry (tests + single-gateway deploy)."""

    _by_token: dict[str, VirtualKeyClaims] = field(default_factory=dict)
    _spend_usd: dict[str, float] = field(default_factory=dict)

    def mint(self, req: MintRequest) -> VirtualKeyClaims:
        key_id = secrets.token_hex(8)
        token = f"vk_{secrets.token_urlsafe(32)}"
        claims = VirtualKeyClaims(
            key_id=key_id,
            token=token,
            tenant_id=req.tenant_id,
            session_id=req.session_id,
            principal_id=req.principal_id,
            allowed_models=tuple(req.allowed_models),
            max_budget_usd=req.max_budget_usd,
            expires_at=time.time() + max(1, req.ttl_s),
            scope_id=req.scope_id,
        )
        self._by_token[token] = claims
        self._spend_usd[token] = 0.0
        return claims

    def get(self, token: str) -> VirtualKeyClaims | None:
        return self._by_token.get(token)

    def add_spend(self, token: str, amount_usd: float) -> float:
        if token not in self._by_token:
            raise LookupError("VIRTUAL_KEY_UNKNOWN")
        spent = self._spend_usd.get(token, 0.0) + max(0.0, float(amount_usd))
        self._spend_usd[token] = spent
        return spent

    def spent_usd(self, token: str) -> float:
        return float(self._spend_usd.get(token, 0.0))

    def revoke(self, token: str) -> None:
        existing = self._by_token.get(token)
        if existing is None:
            return
        self._by_token[token] = VirtualKeyClaims(
            key_id=existing.key_id,
            token=existing.token,
            tenant_id=existing.tenant_id,
            session_id=existing.session_id,
            principal_id=existing.principal_id,
            allowed_models=existing.allowed_models,
            max_budget_usd=existing.max_budget_usd,
            expires_at=existing.expires_at,
            revoked=True,
            scope_id=existing.scope_id,
        )

    def revoke_session(self, session_id: str) -> int:
        n = 0
        for token, claims in list(self._by_token.items()):
            if claims.session_id == session_id and not claims.revoked:
                self.revoke(token)
                n += 1
        return n


def verify_virtual_key(
    store: VirtualKeyStore,
    token: str,
    *,
    route_id: str | None = None,
    now: float | None = None,
) -> VirtualKeyClaims:
    """Fail closed on missing, revoked, expired, or model allowlist miss."""
    claims = store.get(token)
    if claims is None:
        raise LookupError("VIRTUAL_KEY_UNKNOWN")
    if claims.revoked:
        raise PermissionError("VIRTUAL_KEY_REVOKED")
    ts = time.time() if now is None else now
    if ts >= claims.expires_at:
        raise PermissionError("VIRTUAL_KEY_EXPIRED")
    # Empty allowlist = deny all model routes (fail closed, WP-M7).
    if not claims.allowed_models:
        raise PermissionError("VIRTUAL_KEY_MODEL_DENIED")
    if route_id and route_id not in claims.allowed_models:
        raise PermissionError("VIRTUAL_KEY_MODEL_DENIED")
    return claims


def enforce_virtual_key_budget(store: VirtualKeyStore, token: str) -> VirtualKeyClaims:
    """Fail closed when cumulative spend reaches max_budget_usd."""
    claims = store.get(token)
    if claims is None:
        raise LookupError("VIRTUAL_KEY_UNKNOWN")
    spent = store.spent_usd(token)
    if spent >= claims.max_budget_usd:
        raise PermissionError("VIRTUAL_KEY_BUDGET_EXCEEDED")
    return claims
