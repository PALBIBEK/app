"""Where a session's browser lives: the browser code mode's provider seam.

A sandbox profile names its browser (``SandboxProfile.browser``); the session manager asks
that provider, for each ``browser_exec`` call, what the runner in the sandbox must be told
to reach it (``tool_env``). Two so far:

* ``sandbox-chromium`` (the default): a headless Chromium the sandbox's daemon starts;
  nothing to tell. Works with any network, ``none`` included.
* ``remote-cdp``: a browser at a hosted provider (Browserbase's sessions API, which others
  follow), reached over CDP. The API key stays here, in the supervisor: the sandbox gets
  only the session's own connect URL, and must be allowed to reach its host
  (``network: allow``, with that host, or ``open``).
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Final, Protocol
from urllib.parse import urlsplit

import httpx

if TYPE_CHECKING:
    from supervisor.catalog import NetworkAccess

__all__ = [
    "BROWSER_PROVIDERS",
    "BrowserProvider",
    "BrowserUnreachable",
    "RemoteCdp",
    "SandboxChromium",
    "browser_providers_from_env",
]

BROWSER_PROVIDERS: Final[dict[str, frozenset[str]]] = {
    "sandbox-chromium": frozenset({"none", "open", "allow"}),
    "remote-cdp": frozenset({"open", "allow"}),
}
"""Each browser provider, and the sandbox networks it works with."""


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


@dataclass(frozen=True, slots=True)
class SandboxChromium:
    """A headless Chromium in the session's sandbox (its image carries Python, Playwright
    and Chromium), started by the sandbox's daemon on the first call."""

    name: str = "sandbox-chromium"

    async def env(self, session_id: str, network: NetworkAccess | None) -> Mapping[str, str]:
        return {}


SESSION_TIMEOUT_S: Final = 3600
"""How long a remote browser lives at most: the provider ends it then, or when the
sandbox's daemon (its one long connection) goes."""


@dataclass
class RemoteCdp:
    """A browser per session at a hosted provider, through its sessions API (Browserbase's:
    ``POST /v1/sessions`` answers an ``id`` and a ``connectUrl``).

    A session's browser is made on its first ``browser_exec`` call and reused while the
    provider reports it running; ``keepAlive`` is off, so it ends with the sandbox's daemon
    (its one long connection), as a sandbox's own browser ends with its sandbox."""

    api_url: str
    api_key: str
    project_id: str | None = None
    name: str = "remote-cdp"
    client: httpx.AsyncClient = field(default_factory=lambda: httpx.AsyncClient(timeout=30))
    _sessions: dict[str, tuple[str, str]] = field(default_factory=dict)
    """Supervisor session → (provider session id, connect URL)."""
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def env(self, session_id: str, network: NetworkAccess | None) -> Mapping[str, str]:
        async with self._lock:
            known = self._sessions.get(session_id)
            if known is None or not await self._running(known[0]):
                known = self._sessions[session_id] = await self._open(session_id)
        connect = known[1]
        self._check_reachable(connect, network)
        return {"GITHI_BROWSER_CDP": connect}

    async def _open(self, session_id: str) -> tuple[str, str]:
        body: dict[str, Any] = {
            "keepAlive": False,
            "timeout": SESSION_TIMEOUT_S,
            "userMetadata": {"githi_session": session_id},
            **({"projectId": self.project_id} if self.project_id else {}),
        }
        try:
            reply = await self.client.post(f"{self.api_url}/v1/sessions", json=body, headers=self._headers())
            reply.raise_for_status()
            made = reply.json()
            return str(made["id"]), str(made["connectUrl"])
        except (httpx.HTTPError, KeyError, ValueError) as exc:
            raise BrowserUnreachable(f"the remote browser provider did not open a browser: {exc}") from exc

    async def _running(self, provider_session: str) -> bool:
        """Whether the provider still runs it; unknown counts as not (a fresh one is opened)."""
        try:
            reply = await self.client.get(f"{self.api_url}/v1/sessions/{provider_session}", headers=self._headers())
            return reply.status_code == 200 and reply.json().get("status") == "RUNNING"
        except (httpx.HTTPError, ValueError):
            return False

    def _headers(self) -> dict[str, str]:
        return {"X-BB-API-Key": self.api_key}

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


REMOTE_URL_ENV: Final = "REMOTE_BROWSER_API_URL"
REMOTE_KEY_ENV: Final = "REMOTE_BROWSER_API_KEY"
REMOTE_PROJECT_ENV: Final = "REMOTE_BROWSER_PROJECT_ID"


def browser_providers_from_env(environ: Mapping[str, str]) -> dict[str, BrowserProvider]:
    """The deployment's browser providers: the sandbox's own always; ``remote-cdp`` when its
    API is configured (``REMOTE_BROWSER_API_URL``, ``REMOTE_BROWSER_API_KEY``, and
    ``REMOTE_BROWSER_PROJECT_ID`` if the provider wants one)."""
    providers: dict[str, BrowserProvider] = {"sandbox-chromium": SandboxChromium()}
    if (url := environ.get(REMOTE_URL_ENV, "").strip()) and (key := environ.get(REMOTE_KEY_ENV, "").strip()):
        providers["remote-cdp"] = RemoteCdp(url.rstrip("/"), key, environ.get(REMOTE_PROJECT_ENV) or None)
    return providers
