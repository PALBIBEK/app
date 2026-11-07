"""What the browser fills for the agent, never shown to it: saved logins (with their
authenticator key, for the second factor), cards and addresses. Hermes's vault kinds
(``agent/vault_store.py``: login, payment, address), on the tenant's own vault.

A tenant saves each as a tenant secret:

* a site's login, named for the site's origin (``login:https://shop.example.com``), the
  value ``{"username": "…", "password": "…", "otp": "…"}`` (``otp``, optional, is the
  authenticator key: a base32 secret or an ``otpauth://totp`` link);
* a card, named by a label (``card:Visa``), the value its fields
  (``PAYMENT_FIELDS``) and the origins it may be filled on (``"origins": [...]``);
* an address, likewise (``address:Home``, ``ADDRESS_FIELDS``).

Every session of the tenant can then have one filled into a tab on a matching origin by
``browser_vault``. The model names the item (its handle: the secret's name, which holds no
secret); the supervisor reads it when it fills, and the sandbox gets it only for the fill,
in a process the model's code is not part of. A card is filled only once a person allows it.

The page holds what was filled, and code the model writes can read a page, so every tool
result is cleaned of the tenant's passwords, card numbers, security codes and authenticator
keys, and of the one-time codes typed lately, before the journal (and so the model and every
client) gets it: Hermes's model-egress boundary. Encoded copies (base64, reversed) pass it,
as would a screenshot of a page showing a value in clear (a password field shows dots); what
is closed is a secret reaching the model as itself.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Iterable
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Any, Final
from urllib.parse import urlsplit

from supervisor.seams.core.types import split_images, with_images
from supervisor.vault.store import Owner, Vault
from supervisor.vault.totp import InvalidOtpSecret, normalize_otp_secret, totp_now

if TYPE_CHECKING:
    from supervisor.seams.core.types import ToolResult

__all__ = [
    "ADDRESS_FIELDS",
    "ADDRESS_PREFIX",
    "CARD_PREFIX",
    "LOGIN_PREFIX",
    "PAYMENT_FIELDS",
    "VAULT_PREFIXES",
    "BrowserLogins",
    "Checkout",
    "InvalidLogin",
    "Login",
    "TenantLogins",
    "login_secret_name",
    "normalize_origin",
    "parse_item",
    "parse_login",
]

LOGIN_PREFIX: Final = "login:"
CARD_PREFIX: Final = "card:"
ADDRESS_PREFIX: Final = "address:"
VAULT_PREFIXES: Final = (LOGIN_PREFIX, CARD_PREFIX, ADDRESS_PREFIX)
CACHE_S: Final = 60.0
"""How long a tenant's items are reused: a changed password is redacted within this."""
REDACTED: Final = "[redacted:login]"
MIN_REDACTED: Final = 4
"""Shorter values are not redacted: replacing every "abc" in a page would ruin it."""
CODE_REDACTED_S: Final = 600.0
"""How long a one-time code that was typed is cleaned from results."""
_DEFAULT_PORTS: Final = {"http": "80", "https": "443"}
MAX_LABEL: Final = 60

PAYMENT_FIELDS: Final = {
    "card_number": "cc-number",
    "cardholder_name": "cc-name",
    "exp_month": "cc-exp-month",
    "exp_year": "cc-exp-year",
    "cvc": "cc-csc",
    "billing_postal_code": "postal-code",
}
"""A card's fields, and the autocomplete token of the control each fills (Hermes's)."""
ADDRESS_FIELDS: Final = {
    "address_line1": "address-line1",
    "address_line2": "address-line2",
    "city": "address-level2",
    "state": "address-level1",
    "postal_code": "postal-code",
    "country": "country-name",
}
REQUIRED_FIELDS: Final = {"card": ("card_number", "exp_month", "exp_year", "cvc"), "address": ("address_line1", "city", "postal_code", "country")}
SECRET_FIELDS: Final = {"card": ("card_number", "cvc")}
"""The fields of a kind that are secrets (cleaned from results); an address is not one."""


class InvalidLogin(ValueError):
    """An item's name or value is not what a saved login, card or address is."""


@dataclass(frozen=True, slots=True)
class Login:
    origin: str
    username: str
    password: str
    otp: str = ""
    """The authenticator key, normalized (``totp.normalize_otp_secret``); empty: none saved."""

    @property
    def handle(self) -> str:
        return LOGIN_PREFIX + self.origin

    def code(self, at: float | None = None) -> str | None:
        """The second factor's one-time code now, when an authenticator key is saved."""
        return totp_now(self.otp, at=at) if self.otp else None


@dataclass(frozen=True, slots=True)
class Checkout:
    """A saved card (``kind`` "card") or address (``kind`` "address"): its fields, and the
    origins it may be filled on."""

    kind: str
    label: str
    origins: tuple[str, ...]
    fields: dict[str, str] = field(default_factory=dict)

    @property
    def handle(self) -> str:
        return f"{self.kind}:{self.label}"

    @property
    def secrets(self) -> list[str]:
        return [self.fields[k] for k in SECRET_FIELDS.get(self.kind, ()) if self.fields.get(k)]

    def described(self) -> str:
        """How a person recognises it, without its secrets (a card's last four digits)."""
        if self.kind == "card":
            number = "".join(c for c in self.fields.get("card_number", "") if c.isdigit())
            return f"{self.label} (card ending {number[-4:]})" if number else self.label
        return f"{self.label} ({self.fields.get('city', '')})".replace(" ()", "")


def normalize_origin(url: str) -> str:
    """``scheme://host[:port]`` of ``url``, lower-cased, without the scheme's default port
    (as the sandbox's runtime compares it)."""
    parts = urlsplit(url.strip())
    scheme = parts.scheme.lower()
    host, _, port = parts.netloc.lower().rsplit("@", 1)[-1].partition(":")
    if scheme not in _DEFAULT_PORTS or not host:
        raise InvalidLogin(f"{url!r} is not an http(s) origin such as https://shop.example.com")
    return f"{scheme}://{host}" + (f":{port}" if port and port != _DEFAULT_PORTS[scheme] else "")


def login_secret_name(origin: str) -> str:
    return LOGIN_PREFIX + normalize_origin(origin)


def parse_login(name: str, value: str) -> Login:
    """A saved login from its secret; ``InvalidLogin`` when either is not one."""
    origin = normalize_origin(name.removeprefix(LOGIN_PREFIX))
    if LOGIN_PREFIX + origin != name:
        raise InvalidLogin(f"name a login for its origin alone: {LOGIN_PREFIX}{origin}")
    fields = _json_object(value, 'a login is JSON: {"username": "…", "password": "…"} (and "otp": an authenticator key)')
    if not all(isinstance(fields.get(k), str) and fields[k] for k in ("username", "password")):
        raise InvalidLogin('a login holds a "username" and a "password", both text')
    otp = fields.get("otp") or ""
    if not isinstance(otp, str):
        raise InvalidLogin('a login\'s "otp" is its authenticator key, as text')
    try:
        seed = normalize_otp_secret(otp)
    except InvalidOtpSecret as exc:
        raise InvalidLogin(str(exc)) from None
    return Login(origin, fields["username"], fields["password"], seed)


def parse_item(name: str, value: str) -> Login | Checkout:
    """A saved login, card or address from its secret; ``InvalidLogin`` when it is not one."""
    if name.startswith(LOGIN_PREFIX):
        return parse_login(name, value)
    kind = "card" if name.startswith(CARD_PREFIX) else "address" if name.startswith(ADDRESS_PREFIX) else ""
    if not kind:
        raise InvalidLogin(f"not a vault item: names start with {', '.join(VAULT_PREFIXES)}")
    label = name.split(":", 1)[1]
    if not re.fullmatch(rf"[A-Za-z0-9][A-Za-z0-9._-]{{0,{MAX_LABEL - 1}}}", label):
        raise InvalidLogin(f"name a {kind} by a label of letters, digits, '.', '_' or '-' (at most {MAX_LABEL}): {kind}:Visa-4242")
    allowed = PAYMENT_FIELDS if kind == "card" else ADDRESS_FIELDS
    raw = _json_object(value, f'a {kind} is JSON: its fields ({", ".join(allowed)}) and "origins": [...]')
    origins = raw.get("origins")
    if not isinstance(origins, list) or not origins or not all(isinstance(o, str) for o in origins):
        raise InvalidLogin(f'a {kind} names the sites it may be filled on: "origins": ["https://shop.example.com"]')
    fields = {k: str(v).strip() for k, v in raw.items() if k in allowed and str(v or "").strip()}
    missing = [f for f in REQUIRED_FIELDS[kind] if f not in fields]
    if missing:
        raise InvalidLogin(f"a {kind} needs {', '.join(missing)}")
    return Checkout(kind, label, tuple(dict.fromkeys(normalize_origin(o) for o in origins)), fields)


def _json_object(value: str, shape: str) -> dict[str, Any]:
    try:
        fields = json.loads(value)
    except ValueError:
        raise InvalidLogin(shape) from None
    if not isinstance(fields, dict):
        raise InvalidLogin(shape)
    return fields


@dataclass(frozen=True, slots=True)
class TenantItems:
    """One tenant's saved items, by handle."""

    logins: dict[str, Login] = field(default_factory=dict)
    """By origin."""
    checkouts: dict[str, Checkout] = field(default_factory=dict)
    """Cards and addresses, by handle."""

    def secrets(self) -> list[str]:
        values = [v for login in self.logins.values() for v in (login.password, login.otp.split("|", 1)[0]) if v]
        values += [v for item in self.checkouts.values() for v in item.secrets]
        return values


class BrowserLogins:
    """The tenants' saved items, read from the vault (each tenant's cached ``CACHE_S``), and
    the one-time codes typed lately (cleaned from results ``CODE_REDACTED_S``)."""

    __slots__ = ("_cache", "_codes", "_vault")

    def __init__(self, vault: Vault) -> None:
        self._vault = vault
        self._cache: dict[str, tuple[float, TenantItems]] = {}
        self._codes: dict[str, dict[str, float]] = {}

    async def items(self, tenant_id: str) -> TenantItems:
        """The tenant's saved items (a malformed one is left out)."""
        hit = self._cache.get(tenant_id)
        if hit is not None and time.monotonic() - hit[0] < CACHE_S:
            return hit[1]
        owner = Owner.tenant(tenant_id)
        found = TenantItems()
        for name in await self._vault.names(owner):
            if not name.startswith(VAULT_PREFIXES) or (secret := await self._vault.get(owner, name)) is None:
                continue
            try:
                item = parse_item(name, secret.value)
            except InvalidLogin:
                continue
            if isinstance(item, Login):
                found.logins[item.origin] = item
            else:
                found.checkouts[item.handle] = item
        self._cache[tenant_id] = (time.monotonic(), found)
        return found

    async def logins(self, tenant_id: str) -> dict[str, Login]:
        """The tenant's logins by origin."""
        return (await self.items(tenant_id)).logins

    def forget(self, tenant_id: str) -> None:
        """Drop the tenant's cached items (one was saved or deleted through this process)."""
        self._cache.pop(tenant_id, None)

    def typed(self, tenant_id: str, code: str) -> None:
        """A one-time code was typed into a page: clean it from results for a while (a page
        may show it back, or code may read the field)."""
        now = time.monotonic()
        codes = {c: t for c, t in self._codes.get(tenant_id, {}).items() if now - t < CODE_REDACTED_S}
        codes[code] = now
        self._codes[tenant_id] = codes

    def bind(self, tenant_id: str) -> TenantLogins:
        return TenantLogins(self, tenant_id)

    async def redact(self, tenant_id: str, result: ToolResult) -> ToolResult:
        """``result`` with the tenant's saved secrets (and recent codes) replaced wherever they appear."""
        now = time.monotonic()
        codes = [c for c, t in self._codes.get(tenant_id, {}).items() if now - t < CODE_REDACTED_S]
        secrets = sorted(
            {v for v in [*(await self.items(tenant_id)).secrets(), *codes] if len(v) >= MIN_REDACTED}, key=len, reverse=True
        )
        if not secrets:
            return result
        text, images = split_images(result.payload)  # base64 is not text a secret is in
        payload = with_images(_scrub(text, secrets), images)
        error = _scrub(result.error, secrets)
        if payload == result.payload and error == result.error:
            return result
        return replace(result, payload=payload, error=error)


@dataclass(frozen=True, slots=True)
class TenantLogins:
    """One tenant's saved items, as a tool call reaches them (``ToolContext.logins``)."""

    logins: BrowserLogins
    tenant_id: str

    async def origins(self) -> list[str]:
        return sorted(await self.logins.logins(self.tenant_id))

    async def get(self, origin: str) -> Login | None:
        return (await self.logins.logins(self.tenant_id)).get(normalize_origin(origin))

    async def listing(self) -> list[dict[str, Any]]:
        """Every saved item as the agent may see it: its handle, kind and where it is filled,
        a login's username (the agent may type it itself) and whether its second factor is
        automatic; never a password, card number, security code or key (Hermes's ``list``)."""
        items = await self.logins.items(self.tenant_id)
        out: list[dict[str, Any]] = [
            {
                "handle": login.handle,
                "kind": "login",
                "origin": login.origin,
                "identifier": login.username,
                "two_factor": "automatic" if login.otp else "the user is asked for the code",
            }
            for login in sorted(items.logins.values(), key=lambda x: x.origin)
        ]
        out += [
            {"handle": item.handle, "kind": item.kind, "label": item.described(), "origins": list(item.origins)}
            for item in sorted(items.checkouts.values(), key=lambda x: x.handle)
        ]
        return out

    async def item(self, handle: str) -> Login | Checkout | None:
        """The item a handle names (``login:<origin>``, ``card:<label>``, ``address:<label>``);
        a bare origin names its login."""
        items = await self.logins.items(self.tenant_id)
        if handle.startswith(LOGIN_PREFIX) or ("://" in handle and not handle.startswith((CARD_PREFIX, ADDRESS_PREFIX))):
            try:
                return items.logins.get(normalize_origin(handle.removeprefix(LOGIN_PREFIX)))
            except InvalidLogin:
                return None
        return items.checkouts.get(handle)

    def typed(self, code: str) -> None:
        self.logins.typed(self.tenant_id, code)


def _scrub(value: Any, secrets: Iterable[str]) -> Any:
    """``value`` (text, or JSON-shaped data) with every secret replaced by ``REDACTED``."""
    if isinstance(value, str):
        for secret in secrets:
            value = value.replace(secret, REDACTED)
        return value
    if isinstance(value, dict):
        return {k: _scrub(v, secrets) for k, v in value.items()}
    if isinstance(value, list):
        return [_scrub(v, secrets) for v in value]
    return value
