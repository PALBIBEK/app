"""Where a session's browser lives: the browser code mode's provider seam.

A sandbox profile names its browser (``SandboxProfile.browser``); the session manager asks
that provider, for each browser tool call, what the runner in the sandbox must be told to
reach it (``tool_env``), and tells it when the session no longer needs it (``release``).

In the sandbox (any network, ``none`` included):

* ``sandbox-chromium`` (the default): a headless Chromium the sandbox's daemon starts;
* ``sandbox-lightpanda``: Lightpanda (https://lightpanda.io), a lightweight headless
  engine for text-first browsing, when the sandbox's image has it (Hermes's
  ``browser.engine: lightpanda``).

At a hosted provider, reached over CDP (Hermes's cloud browser plugins,
``plugins/browser/*``): the API key stays here, in the supervisor; the sandbox gets only the
session's own connect URL, and must be allowed to reach its host (``network: allow`` with
that host, or ``open``):

* ``browserbase``: Browserbase, with Hermes's defaults (residential proxies on, advanced
  stealth off, keep-alive on) and its fallback when a plan lacks a paid feature (402: keep-
  alive dropped, then proxies);
* ``browser-use``: Browser Use's cloud (stealth and residential proxies are its own);
* ``firecrawl``: Firecrawl's browser sessions;
* ``remote-cdp``: any provider with Browserbase's sessions API, at an address of the
  deployment's choosing.

A hosted browser opened for a session is reused while the provider keeps it, and released
when the session ends or its sandbox stops: a provider bills by the minute.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any, ClassVar, Final, Protocol
from urllib.parse import urlsplit

import httpx

if TYPE_CHECKING:
    from supervisor.catalog import NetworkAccess

__all__ = [
    "BROWSER_PROVIDERS",
    "HOSTED_BROWSERS",
    "BrowserProvider",
    "BrowserUnreachable",
    "BrowserUse",
    "Browserbase",
    "CloudBrowser",
    "Firecrawl",
    "RemoteCdp",
    "SandboxChromium",
    "SandboxLightpanda",
    "browser_providers_from_env",
]

logger = logging.getLogger(__name__)

_IN_SANDBOX: Final = frozenset({"none", "open", "allow"})
_HOSTED: Final = frozenset({"open", "allow"})
BROWSER_PROVIDERS: Final[dict[str, frozenset[str]]] = {
    "sandbox-chromium": _IN_SANDBOX,
    "sandbox-lightpanda": _IN_SANDBOX,
    "browserbase": _HOSTED,
    "browser-use": _HOSTED,
    "firecrawl": _HOSTED,
    "remote-cdp": _HOSTED,
}
"""Each browser provider, and the sandbox networks it works with."""
HOSTED_BROWSERS: Final = frozenset(
    name for name, networks in BROWSER_PROVIDERS.items() if networks == _HOSTED
)

ENGINE_ENV: Final = "GITHI_BROWSER_ENGINE"
"""Which engine the sandbox's daemon starts (``lightpanda``; unset: Chromium)."""


class BrowserUnreachable(RuntimeError):
    """The session's browser provider cannot serve it (not configured, refused, or its
    host is not one the sandbox may reach)."""


class BrowserProvider(Protocol):
    """What the runner in a session's sandbox needs to reach the session's browser."""

    @property
    def name(self) -> str: ...

    async def env(self, session_id: str, network: NetworkAccess | None) -> Mapping[str, str]:
        """Variables for the runner (``GITHI_BROWSER_CDP``: a browser to connect to); none
        means the sandbox starts its own."""
        ...

    async def release(self, session_id: str) -> None:
        """The session no longer needs its browser (it ended, or its sandbox stopped)."""
        ...


@dataclass(frozen=True, slots=True)
class SandboxChromium:
    """A headless Chromium in the session's sandbox (its image carries Python, Playwright
    and Chromium), started by the sandbox's daemon on the first call."""

    name: str = "sandbox-chromium"

    async def env(self, session_id: str, network: NetworkAccess | None) -> Mapping[str, str]:
        return {}

    async def release(self, session_id: str) -> None:
        """It goes with the sandbox."""


@dataclass(frozen=True, slots=True)
class SandboxLightpanda:
    """Lightpanda in the session's sandbox, started by its daemon (``lightpanda serve``):
    the image must carry the ``lightpanda`` binary (``LIGHTPANDA_PATH``, or on ``PATH``)."""

    name: str = "sandbox-lightpanda"

    async def env(self, session_id: str, network: NetworkAccess | None) -> Mapping[str, str]:
        return {ENGINE_ENV: "lightpanda"}

    async def release(self, session_id: str) -> None:
        """It goes with the sandbox."""


SESSION_TIMEOUT_S: Final = 3600
"""How long a hosted browser lives at most, by default: the provider ends it then."""


@dataclass(slots=True)
class OpenBrowser:
    """A hosted browser opened for one session."""

    id: str
    connect_url: str
    expires_at: float | None = None
    """When the provider ends it (``time.time()``), if it says."""


@dataclass
class CloudBrowser:
    """Hermes's ``CloudBrowserProvider`` (``plugins/browser/_common.py``): every hosted
    provider speaks one shape (a POST opens a session, one request releases it). A
    subclass sets ``name``, ``label`` and its headers, and opens, checks and releases."""

    api_url: str
    api_key: str
    client: httpx.AsyncClient = field(default_factory=lambda: httpx.AsyncClient(timeout=30))
    _sessions: dict[str, OpenBrowser] = field(default_factory=dict)
    """Supervisor session -> its browser at the provider."""
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    idle_s: int = 300
    """The sandbox's daemon lets the browser go after this long without a call (Hermes's
    inactivity timeout: a provider bills by the minute); the next call opens another."""

    name: ClassVar[str] = ""
    label: ClassVar[str] = ""

    async def env(self, session_id: str, network: NetworkAccess | None) -> Mapping[str, str]:
        async with self._lock:
            known = self._sessions.get(session_id)
            if known is None or not await self._alive(known):
                known = self._sessions[session_id] = await self._open_checked(session_id)
        self._check_reachable(known.connect_url, network)
        return {"GITHI_BROWSER_CDP": known.connect_url, "GITHI_BROWSER_IDLE_S": str(self.idle_s)}

    async def release(self, session_id: str) -> None:
        async with self._lock:
            known = self._sessions.pop(session_id, None)
        if known is None:
            return
        try:
            reply = await self._release(known.id)
            if reply.status_code not in (200, 201, 204):
                logger.warning(
                    "releasing the %s browser of session %s: HTTP %s",
                    self.label,
                    session_id,
                    reply.status_code,
                )
        except httpx.HTTPError as exc:
            logger.warning(
                "releasing the %s browser of session %s failed: %s", self.label, session_id, exc
            )

    async def _open_checked(self, session_id: str) -> OpenBrowser:
        try:
            return await self._open(session_id)
        except (httpx.HTTPError, KeyError, ValueError, TypeError) as exc:
            raise BrowserUnreachable(f"{self.label} did not open a browser: {exc}") from exc

    async def _open(self, session_id: str) -> OpenBrowser:
        raise NotImplementedError

    async def _alive(self, browser: OpenBrowser) -> bool:
        """Whether the provider still runs it: by the expiry it gave, by default."""
        return browser.expires_at is None or browser.expires_at > time.time() + 30

    async def _release(self, browser_id: str) -> httpx.Response:
        raise NotImplementedError

    def _headers(self) -> dict[str, str]:
        raise NotImplementedError

    async def _post(self, path: str, body: dict[str, Any]) -> httpx.Response:
        return await self.client.post(f"{self.api_url}{path}", json=body, headers=self._headers())

    @staticmethod
    def _check_reachable(connect: str, network: NetworkAccess | None) -> None:
        host = urlsplit(connect).hostname or ""
        mode = str(getattr(network.mode, "value", network.mode)) if network else "none"
        if mode == "open" or (mode == "allow" and network is not None and host in network.hosts):
            return
        raise BrowserUnreachable(
            f"the remote browser is at {host}, which this sandbox may not reach: "
            f"its profile needs network allow with {host!r} in hosts (or open)"
        )


_PAID_FEATURE_FALLBACKS: Final = (
    (
        "keepAlive",
        "keepAlive may need a paid plan (402): retrying without it; a session may end during a long step",
    ),
    (
        "proxies",
        "proxies unavailable (402): retrying without them; bot detection may be less effective",
    ),
)
"""Hermes's 402 fallbacks, in the order they are tried: the feature dropped, and why."""


@dataclass
class Browserbase(CloudBrowser):
    """Browserbase (https://browserbase.com), as Hermes's plugin opens it: basic stealth always
    (theirs), residential proxies on, advanced stealth off (a Scale plan's), keep-alive on (the
    session survives a dropped connection; released when the session ends)."""

    project_id: str | None = None
    proxies: bool = True
    advanced_stealth: bool = False
    keep_alive: bool = True
    timeout_s: int = SESSION_TIMEOUT_S

    name: ClassVar[str] = "browserbase"
    label: ClassVar[str] = "Browserbase"

    def _headers(self) -> dict[str, str]:
        return {"Content-Type": "application/json", "X-BB-API-Key": self.api_key}

    async def _open(self, session_id: str) -> OpenBrowser:
        body: dict[str, Any] = {
            "keepAlive": self.keep_alive,
            "timeout": self.timeout_s,
            "userMetadata": {"githi_session": session_id},
            **({"projectId": self.project_id} if self.project_id else {}),
        }
        if self.proxies:
            body["proxies"] = True
        if self.advanced_stealth:
            body["browserSettings"] = {"advancedStealth": True}
        reply = await self._post("/v1/sessions", body)
        for key, why in _PAID_FEATURE_FALLBACKS:
            if reply.status_code == 402 and body.get(key):
                logger.warning("Browserbase: %s", why)
                body.pop(key)
                reply = await self._post("/v1/sessions", body)
        reply.raise_for_status()
        made = reply.json()
        return OpenBrowser(str(made["id"]), str(made["connectUrl"]))

    async def _alive(self, browser: OpenBrowser) -> bool:
        """Whether Browserbase still runs it; unknown counts as not (a fresh one is opened)."""
        try:
            reply = await self.client.get(
                f"{self.api_url}/v1/sessions/{browser.id}", headers=self._headers()
            )
            return reply.status_code == 200 and reply.json().get("status") == "RUNNING"
        except (httpx.HTTPError, ValueError):
            return False

    async def _release(self, browser_id: str) -> httpx.Response:
        body: dict[str, Any] = {
            "status": "REQUEST_RELEASE",
            **({"projectId": self.project_id} if self.project_id else {}),
        }
        return await self._post(f"/v1/sessions/{browser_id}", body)


@dataclass
class RemoteCdp(Browserbase):
    """Any provider with Browserbase's sessions API, at the deployment's address: opened
    plain (no proxies, no keep-alive), so it ends with the sandbox daemon's connection."""

    proxies: bool = False
    keep_alive: bool = False

    name: ClassVar[str] = "remote-cdp"
    label: ClassVar[str] = "The remote browser provider"


@dataclass
class BrowserUse(CloudBrowser):
    """Browser Use's cloud (https://browser-use.com), direct key (Hermes's plugin; its Nous
    gateway mode is Nous's own): a browser per session, its lifetime set by the provider."""

    name: ClassVar[str] = "browser-use"
    label: ClassVar[str] = "Browser Use"

    def _headers(self) -> dict[str, str]:
        return {"Content-Type": "application/json", "X-Browser-Use-API-Key": self.api_key}

    async def _open(self, session_id: str) -> OpenBrowser:
        reply = await self._post("/browsers", {})
        reply.raise_for_status()
        made = reply.json()
        connect = made.get("cdpUrl") or made.get("connectUrl")
        if not connect:
            raise ValueError("the answer named no CDP URL")
        return OpenBrowser(str(made["id"]), str(connect), _timestamp(made.get("timeoutAt")))

    async def _release(self, browser_id: str) -> httpx.Response:
        return await self.client.patch(
            f"{self.api_url}/browsers/{browser_id}",
            json={"action": "stop"},
            headers=self._headers(),
        )


@dataclass
class Firecrawl(CloudBrowser):
    """Firecrawl's browser sessions (https://firecrawl.dev, ``/v2/browser``), each living
    ``ttl_s`` seconds."""

    ttl_s: int = 300

    name: ClassVar[str] = "firecrawl"
    label: ClassVar[str] = "Firecrawl"

    def _headers(self) -> dict[str, str]:
        return {"Content-Type": "application/json", "Authorization": f"Bearer {self.api_key}"}

    async def _open(self, session_id: str) -> OpenBrowser:
        reply = await self._post("/v2/browser", {"ttl": self.ttl_s})
        reply.raise_for_status()
        made = reply.json()
        return OpenBrowser(str(made["id"]), str(made["cdpUrl"]), time.time() + self.ttl_s)

    async def _release(self, browser_id: str) -> httpx.Response:
        return await self.client.delete(
            f"{self.api_url}/v2/browser/{browser_id}", headers=self._headers()
        )


def _timestamp(value: Any) -> float | None:
    """An ISO time a provider gave, as ``time.time()``; None if it gave none."""
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value).timestamp()  # Python 3.11+ reads a trailing Z
    except ValueError:
        return None


REMOTE_URL_ENV: Final = "REMOTE_BROWSER_API_URL"
REMOTE_KEY_ENV: Final = "REMOTE_BROWSER_API_KEY"
REMOTE_PROJECT_ENV: Final = "REMOTE_BROWSER_PROJECT_ID"


def browser_providers_from_env(environ: Mapping[str, str]) -> dict[str, BrowserProvider]:
    """The deployment's browser providers: the sandbox's own always (Chromium, Lightpanda);
    each hosted one whose credentials are set (Hermes's variables):

    * Browserbase: ``BROWSERBASE_API_KEY`` and ``BROWSERBASE_PROJECT_ID``; optional
      ``BROWSERBASE_BASE_URL``, ``BROWSERBASE_PROXIES`` (true), ``BROWSERBASE_ADVANCED_STEALTH``
      (false), ``BROWSERBASE_KEEP_ALIVE`` (true), ``BROWSERBASE_SESSION_TIMEOUT`` (seconds);
    * Browser Use: ``BROWSER_USE_API_KEY`` (``BROWSER_USE_BASE_URL``);
    * Firecrawl: ``FIRECRAWL_API_KEY`` (``FIRECRAWL_API_URL``, ``FIRECRAWL_BROWSER_TTL`` seconds);
    * ``remote-cdp``: ``REMOTE_BROWSER_API_URL`` and ``REMOTE_BROWSER_API_KEY`` (and
      ``REMOTE_BROWSER_PROJECT_ID`` if the provider wants one).

    ``BROWSER_INACTIVITY_TIMEOUT`` (seconds, 300) lets a hosted browser go after that long
    without a call.
    """
    env = {k: v.strip() for k, v in environ.items()}
    idle = _positive(env.get("BROWSER_INACTIVITY_TIMEOUT"), 300)
    providers: dict[str, BrowserProvider] = {
        "sandbox-chromium": SandboxChromium(),
        "sandbox-lightpanda": SandboxLightpanda(),
    }
    if (key := env.get("BROWSERBASE_API_KEY")) and (project := env.get("BROWSERBASE_PROJECT_ID")):
        providers["browserbase"] = Browserbase(
            (env.get("BROWSERBASE_BASE_URL") or "https://api.browserbase.com").rstrip("/"),
            key,
            project_id=project,
            proxies=env.get("BROWSERBASE_PROXIES", "true").lower() != "false",
            advanced_stealth=env.get("BROWSERBASE_ADVANCED_STEALTH", "false").lower() == "true",
            keep_alive=env.get("BROWSERBASE_KEEP_ALIVE", "true").lower() != "false",
            timeout_s=_positive(env.get("BROWSERBASE_SESSION_TIMEOUT"), SESSION_TIMEOUT_S),
            idle_s=idle,
        )
    if key := env.get("BROWSER_USE_API_KEY"):
        providers["browser-use"] = BrowserUse(
            (env.get("BROWSER_USE_BASE_URL") or "https://api.browser-use.com/api/v3").rstrip("/"),
            key,
            idle_s=idle,
        )
    if key := env.get("FIRECRAWL_API_KEY"):
        providers["firecrawl"] = Firecrawl(
            (env.get("FIRECRAWL_API_URL") or "https://api.firecrawl.dev").rstrip("/"),
            key,
            ttl_s=_positive(env.get("FIRECRAWL_BROWSER_TTL"), 300),
            idle_s=idle,
        )
    if (url := env.get(REMOTE_URL_ENV)) and (key := env.get(REMOTE_KEY_ENV)):
        providers["remote-cdp"] = RemoteCdp(
            url.rstrip("/"), key, project_id=env.get(REMOTE_PROJECT_ENV) or None, idle_s=idle
        )
    return providers


def _positive(value: str | None, default: int) -> int:
    try:
        number = int(value) if value else default
    except ValueError:
        logger.warning("not a number of seconds: %r; using %s", value, default)
        return default
    return number if number > 0 else default
