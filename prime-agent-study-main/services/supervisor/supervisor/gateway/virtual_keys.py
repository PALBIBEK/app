"""Session-scoped virtual keys — provider secrets stay in the gateway process.

WP-M5: mint with the shared service token; chat accepts ``vk_…`` tokens.

``SignedVirtualKeyStore`` (what the gateway runs): a key carries its own claims,
signed, so any gateway process that holds the secret takes it, before and after a
restart. Revocations and per-key spend are kept in the process: after a restart a
revoked key works again until it expires (a terminated session's key is held only by
the supervisor, which has forgotten it), and the session's cost cap, which the
supervisor enforces, is the bound that holds across restarts.

``MemoryVirtualKeyStore``: an in-process registry (tests).
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import secrets
import time
from dataclasses import dataclass, field, replace
from typing import Any, Final, Protocol, runtime_checkable

KEY_TTL_S: Final = 900
"""How long a session key lives (15 minutes). Revoking a key, or a session's keys, lasts only
as long as the gateway process that was told: a restart forgets it, and the key works again
until it expires. Short keys bound that window, and a key's per-key spend, which a restart
resets too; the supervisor renews each in its last few minutes, so no session notices."""


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
    issued_at: float = 0.0


@dataclass
class MintRequest:
    tenant_id: str
    session_id: str
    principal_id: str
    allowed_models: tuple[str, ...] = ()
    ttl_s: int = KEY_TTL_S
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
            issued_at=time.time(),
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
        self._by_token[token] = replace(existing, revoked=True)

    def revoke_session(self, session_id: str) -> int:
        n = 0
        for token, claims in list(self._by_token.items()):
            if claims.session_id == session_id and not claims.revoked:
                self.revoke(token)
                n += 1
        return n


_PREFIX: Final = "vk_"
_SIGNED_AS: Final = b"vk1."
"""What a signature covers before the claims: a key format version, so a later format
never verifies as this one."""


def derive_virtual_key_secret(service_token: str) -> bytes:
    """The signing secret, from the service token every gateway process already shares:
    whoever holds that token can mint keys anyway, so it may sign them too."""
    return hmac.new(service_token.encode(), b"yal virtual keys v1", hashlib.sha256).digest()


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


@dataclass
class SignedVirtualKeyStore:
    """Keys that carry their claims, signed with ``secret`` (HMAC-SHA256).

    Taken by any process with the same secret, so a restart or a second replica does
    not cut a session off. A key is ``vk_<claims>.<signature>`` (base64url).
    """

    secret: bytes
    _revoked: set[str] = field(default_factory=set)  # key ids
    _sessions_revoked: dict[str, float] = field(default_factory=dict)  # session -> when
    _minted: dict[str, dict[str, float]] = field(default_factory=dict)  # session -> key id -> expiry
    _spend_usd: dict[str, float] = field(default_factory=dict)  # key id -> spent

    def __post_init__(self) -> None:
        if len(self.secret) < 32:
            raise ValueError("a virtual key secret needs at least 32 bytes")

    def mint(self, req: MintRequest) -> VirtualKeyClaims:
        now = time.time()
        body: dict[str, Any] = {
            "kid": secrets.token_hex(8),
            "ten": req.tenant_id,
            "ses": req.session_id,
            "pri": req.principal_id,
            "mod": list(req.allowed_models),
            "bud": req.max_budget_usd,
            "iat": now,
            "exp": now + max(1, req.ttl_s),
            "sco": req.scope_id,
        }
        payload = _b64(json.dumps(body, separators=(",", ":"), sort_keys=True).encode())
        token = f"{_PREFIX}{payload}.{_b64(self._sign(payload))}"
        claims = self._claims(token, body)
        live = self._minted.setdefault(req.session_id, {})
        for key_id, expires in list(live.items()):  # what this process minted, still live
            if expires <= now:
                del live[key_id]
        live[claims.key_id] = claims.expires_at
        return claims

    def get(self, token: str) -> VirtualKeyClaims | None:
        """The key's claims, or None when it is not one this secret signed."""
        if not token.startswith(_PREFIX):
            return None
        payload, dot, signature = token[len(_PREFIX):].partition(".")
        if not dot:
            return None
        try:
            given = _unb64(signature)
            body = json.loads(_unb64(payload))
        except (ValueError, binascii.Error):
            return None
        if not hmac.compare_digest(given, self._sign(payload)) or not isinstance(body, dict):
            return None
        try:
            return self._claims(token, body)
        except (KeyError, TypeError, ValueError):
            return None

    def revoke(self, token: str) -> None:
        if (claims := self.get(token)) is not None:
            self._revoked.add(claims.key_id)

    def revoke_session(self, session_id: str) -> int:
        """Revoke every key of the session minted until now; how many this process minted."""
        self._sessions_revoked[session_id] = time.time()
        return len(self._minted.pop(session_id, {}))

    def add_spend(self, token: str, amount_usd: float) -> float:
        claims = self.get(token)
        if claims is None:
            raise LookupError("VIRTUAL_KEY_UNKNOWN")
        spent = self._spend_usd.get(claims.key_id, 0.0) + max(0.0, float(amount_usd))
        self._spend_usd[claims.key_id] = spent
        return spent

    def spent_usd(self, token: str) -> float:
        claims = self.get(token)
        return 0.0 if claims is None else float(self._spend_usd.get(claims.key_id, 0.0))

    def _sign(self, payload: str) -> bytes:
        return hmac.new(self.secret, _SIGNED_AS + payload.encode(), hashlib.sha256).digest()

    def _claims(self, token: str, body: dict[str, Any]) -> VirtualKeyClaims:
        claims = VirtualKeyClaims(
            key_id=str(body["kid"]),
            token=token,
            tenant_id=str(body["ten"]),
            session_id=str(body["ses"]),
            principal_id=str(body["pri"]),
            allowed_models=tuple(str(m) for m in body["mod"]),
            max_budget_usd=float(body["bud"]),
            expires_at=float(body["exp"]),
            scope_id=None if body.get("sco") is None else str(body["sco"]),
            issued_at=float(body["iat"]),
        )
        revoked_at = self._sessions_revoked.get(claims.session_id)
        if claims.key_id in self._revoked or (revoked_at is not None and claims.issued_at <= revoked_at):
            return replace(claims, revoked=True)
        return claims


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
