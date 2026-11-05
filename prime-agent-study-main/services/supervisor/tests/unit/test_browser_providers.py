"""The browser provider seam: where a session's browser lives, and what the sandbox is
told to reach it (the hosted provider's API faked: Browserbase's sessions API)."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from supervisor.catalog import NetworkAccess, NetworkMode
from supervisor.hands.browser import (
    BrowserUnreachable,
    RemoteCdp,
    SandboxChromium,
    browser_providers_from_env,
)

pytestmark = pytest.mark.asyncio
ALLOWED = NetworkAccess(NetworkMode.ALLOW, ("connect.browser.test",))


class Provider:
    """A fake sessions API: opens sessions, says whether each still runs."""

    def __init__(self) -> None:
        self.opened: list[dict[str, Any]] = []
        self.running: set[str] = set()

    def __call__(self, request: httpx.Request) -> httpx.Response:
        assert request.headers["X-BB-API-Key"] == "secret-key"
        if request.method == "POST":
            body = json.loads(request.content)
            sid = f"bb-{len(self.opened)}"
            self.opened.append(body)
            self.running.add(sid)
            return httpx.Response(200, json={"id": sid, "connectUrl": f"wss://connect.browser.test?signingKey={sid}"})
        sid = request.url.path.rsplit("/", 1)[-1]
        return httpx.Response(200, json={"status": "RUNNING" if sid in self.running else "COMPLETED"})


def _remote(api: Provider) -> RemoteCdp:
    return RemoteCdp("https://api.browser.test", "secret-key", "proj-1", client=httpx.AsyncClient(transport=httpx.MockTransport(api)))


async def test_the_sandbox_gets_a_session_url_never_the_key() -> None:
    api = Provider()

    env = await _remote(api).env("s1", ALLOWED)

    assert env == {"GITHI_BROWSER_CDP": "wss://connect.browser.test?signingKey=bb-0"}
    assert api.opened == [{"keepAlive": False, "timeout": 3600, "userMetadata": {"githi_session": "s1"}, "projectId": "proj-1"}]


async def test_a_session_keeps_its_browser_while_it_runs_and_gets_another_after() -> None:
    api = Provider()
    remote = _remote(api)
    first = await remote.env("s1", ALLOWED)
    again = await remote.env("s1", ALLOWED)
    api.running.clear()  # the provider ended it (the sandbox's daemon went)
    after = await remote.env("s1", ALLOWED)
    other = await remote.env("s2", ALLOWED)

    assert first == again and after != first and len(api.opened) == 3
    assert other["GITHI_BROWSER_CDP"].endswith("bb-2")


@pytest.mark.parametrize(
    "network",
    [None, NetworkAccess(NetworkMode.NONE), NetworkAccess(NetworkMode.ALLOW, ("somewhere.else",))],
)
async def test_a_remote_browser_the_sandbox_may_not_reach_is_refused(network: NetworkAccess | None) -> None:
    with pytest.raises(BrowserUnreachable, match="connect.browser.test"):
        await _remote(Provider()).env("s1", network)


async def test_an_open_sandbox_reaches_any_remote_browser() -> None:
    assert await _remote(Provider()).env("s1", NetworkAccess(NetworkMode.OPEN))


async def test_a_provider_that_refuses_says_so() -> None:
    refusing = RemoteCdp("https://api.browser.test", "k", client=httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(402))))

    with pytest.raises(BrowserUnreachable, match="did not open a browser"):
        await refusing.env("s1", ALLOWED)


async def test_the_sandboxs_own_browser_needs_nothing() -> None:
    assert await SandboxChromium().env("s1", None) == {}


async def test_the_deployment_offers_a_remote_browser_only_when_configured() -> None:
    assert set(browser_providers_from_env({})) == {"sandbox-chromium"}
    configured = browser_providers_from_env({"REMOTE_BROWSER_API_URL": "https://api.browser.test/", "REMOTE_BROWSER_API_KEY": "k"})
    remote = configured["remote-cdp"]
    assert isinstance(remote, RemoteCdp) and remote.api_url == "https://api.browser.test"
