"""Saved logins for the browser: typed into a page for the agent, never shown to it.

A tenant saves a site's login once, as a tenant secret named for the site's origin
(``PUT /v1/secrets/login:https://shop.example.com``, the value
``{"username": "…", "password": "…"}``). Every session of the tenant can then have it
typed into a tab on that origin by ``browser_vault``. The model names the login and the
fields; the supervisor reads the secret when it fills, and the sandbox gets it only for
the fill, in a process the model's code is not part of.

The page holds the password once it is typed, and code the model writes can read a page,
so every tool result is cleaned of the tenant's saved passwords before the journal (and so
the model and every client) gets it: Hermes's model-egress boundary. Encoded copies
(base64, reversed) pass it; what is closed is the password reaching the model as itself.
"""

from __future__ import annotations

import json
import time
from collections.abc import Iterable
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any, Final
from urllib.parse import urlsplit

from supervisor.vault.store import Owner, Vault

if TYPE_CHECKING:
    from supervisor.seams.core.types import ToolResult

__all__ = [
    "LOGIN_PREFIX",
    "BrowserLogins",
    "InvalidLogin",
    "Login",
    "TenantLogins",
    "login_secret_name",
    "normalize_origin",
    "parse_login",
]

LOGIN_PREFIX: Final = "login:"
CACHE_S: Final = 60.0
"""How long a tenant's logins are reused: a changed password is redacted within this."""
REDACTED: Final = "[redacted:login]"
MIN_REDACTED: Final = 4
"""Shorter passwords are not redacted: replacing every "abc" in a page would ruin it."""
_DEFAULT_PORTS: Final = {"http": "80", "https": "443"}


class InvalidLogin(ValueError):
    """A login's name or value is not what a saved login is."""


@dataclass(frozen=True, slots=True)
class Login:
    origin: str
    username: str
    password: str


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
    try:
        fields = json.loads(value)
    except ValueError:
        raise InvalidLogin('a login is JSON: {"username": "…", "password": "…"}') from None
    if not isinstance(fields, dict) or not all(isinstance(fields.get(k), str) and fields[k] for k in ("username", "password")):
        raise InvalidLogin('a login holds a "username" and a "password", both text')
    return Login(origin, fields["username"], fields["password"])


class BrowserLogins:
    """The tenants' saved logins, read from the vault (each tenant's cached ``CACHE_S``)."""

    __slots__ = ("_cache", "_vault")

    def __init__(self, vault: Vault) -> None:
        self._vault = vault
        self._cache: dict[str, tuple[float, dict[str, Login]]] = {}

    async def logins(self, tenant_id: str) -> dict[str, Login]:
        """The tenant's logins by origin (a malformed one is left out)."""
        hit = self._cache.get(tenant_id)
        if hit is not None and time.monotonic() - hit[0] < CACHE_S:
            return hit[1]
        owner = Owner.tenant(tenant_id)
        found: dict[str, Login] = {}
        for name in await self._vault.names(owner):
            if not name.startswith(LOGIN_PREFIX) or (secret := await self._vault.get(owner, name)) is None:
                continue
            try:
                login = parse_login(name, secret.value)
            except InvalidLogin:
                continue
            found[login.origin] = login
        self._cache[tenant_id] = (time.monotonic(), found)
        return found

    def forget(self, tenant_id: str) -> None:
        """Drop the tenant's cached logins (one was saved or deleted through this process)."""
        self._cache.pop(tenant_id, None)

    def bind(self, tenant_id: str) -> TenantLogins:
        return TenantLogins(self, tenant_id)

    async def redact(self, tenant_id: str, result: ToolResult) -> ToolResult:
        """``result`` with the tenant's saved passwords replaced wherever they appear."""
        secrets = [login.password for login in (await self.logins(tenant_id)).values() if len(login.password) >= MIN_REDACTED]
        if not secrets:
            return result
        payload, error = _scrub(result.payload, secrets), _scrub(result.error, secrets)
        if payload == result.payload and error == result.error:
            return result
        return replace(result, payload=payload, error=error)


@dataclass(frozen=True, slots=True)
class TenantLogins:
    """One tenant's logins, as a tool call reaches them (``ToolContext.logins``)."""

    logins: BrowserLogins
    tenant_id: str

    async def origins(self) -> list[str]:
        return sorted(await self.logins.logins(self.tenant_id))

    async def get(self, origin: str) -> Login | None:
        return (await self.logins.logins(self.tenant_id)).get(normalize_origin(origin))


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
