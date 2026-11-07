"""The browser provider seam: where a session's browser lives, and what the sandbox is
told to reach it (the hosted provider's API faked: Browserbase's sessions API)."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from supervisor.catalog import NetworkAccess, NetworkMode
from supervisor.hands.browser import (
    Browserbase,
    BrowserUnreachable,
    BrowserUse,
    Firecrawl,
    RemoteCdp,
    SandboxChromium,
    SandboxLightpanda,
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
    return RemoteCdp("https://api.browser.test", "secret-key", project_id="proj-1", client=httpx.AsyncClient(transport=httpx.MockTransport(api)))


async def test_the_sandbox_gets_a_session_url_never_the_key() -> None:
    api = Provider()

    env = await _remote(api).env("s1", ALLOWED)

    assert env == {"GITHI_BROWSER_CDP": "wss://connect.browser.test?signingKey=bb-0", "GITHI_BROWSER_IDLE_S": "300"}
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
    assert set(browser_providers_from_env({})) == {"sandbox-chromium", "sandbox-lightpanda"}
    configured = browser_providers_from_env({"REMOTE_BROWSER_API_URL": "https://api.browser.test/", "REMOTE_BROWSER_API_KEY": "k"})
    remote = configured["remote-cdp"]
    assert isinstance(remote, RemoteCdp) and remote.api_url == "https://api.browser.test"



# Hermes's cloud browser plugins ---------------------------------------------------------------


class Api:
    """A fake hosted provider: records every request; ``answers`` (method, path) -> response."""

    def __init__(self, answers: dict[tuple[str, str], Any]) -> None:
        self.answers = answers
        self.seen: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.seen.append(request)
        answer = self.answers[(request.method, request.url.path)]
        return answer(request) if callable(answer) else answer

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self))

    def bodies(self, method: str = "POST") -> list[Any]:
        return [json.loads(r.content) for r in self.seen if r.method == method and r.content]


OPEN = NetworkAccess(NetworkMode.OPEN)


async def test_browserbase_opens_with_hermess_defaults_and_drops_paid_features_on_402() -> None:
    """Basic stealth always, proxies and keep-alive on; a plan without them answers 402:
    keep-alive is dropped first, then proxies (Hermes's _PAID_FEATURE_FALLBACKS)."""
    answers = iter([httpx.Response(402), httpx.Response(402), httpx.Response(200, json={"id": "bb-1", "connectUrl": "wss://connect.bb.test/x"})])
    api = Api({("POST", "/v1/sessions"): lambda _: next(answers)})
    bb = Browserbase("https://bb.test", "key", project_id="p1", client=api.client())

    env = await bb.env("s1", OPEN)

    first, second, third = api.bodies()
    assert first["keepAlive"] is True and first["proxies"] is True and first["projectId"] == "p1"
    assert "keepAlive" not in second and second["proxies"] is True
    assert "keepAlive" not in third and "proxies" not in third
    assert env["GITHI_BROWSER_CDP"] == "wss://connect.bb.test/x"
    assert all(r.headers["X-BB-API-Key"] == "key" for r in api.seen)


async def test_browserbase_advanced_stealth_is_asked_for_only_when_set() -> None:
    api = Api({("POST", "/v1/sessions"): httpx.Response(200, json={"id": "bb-1", "connectUrl": "wss://c.test/x"})})
    await Browserbase("https://bb.test", "k", project_id="p", advanced_stealth=True, proxies=False, client=api.client()).env("s1", OPEN)

    (body,) = api.bodies()
    assert body["browserSettings"] == {"advancedStealth": True} and "proxies" not in body


async def test_a_hosted_browser_is_released_when_its_session_is_done() -> None:
    api = Api({
        ("POST", "/v1/sessions"): httpx.Response(200, json={"id": "bb-9", "connectUrl": "wss://c.test/x"}),
        ("POST", "/v1/sessions/bb-9"): httpx.Response(200, json={}),
    })
    bb = Browserbase("https://bb.test", "k", project_id="p", client=api.client())
    await bb.env("s1", OPEN)

    await bb.release("s1")
    await bb.release("s1")  # once only

    releases = [json.loads(r.content) for r in api.seen if r.url.path == "/v1/sessions/bb-9"]
    assert releases == [{"status": "REQUEST_RELEASE", "projectId": "p"}]


async def test_browser_use_opens_a_browser_reuses_it_until_its_timeout_and_stops_it() -> None:
    opened = iter(["2099-01-01T00:00:00Z", "2000-01-01T00:00:00Z", "2099-01-01T00:00:00Z"])
    api = Api({
        ("POST", "/browsers"): lambda _: httpx.Response(200, json={"id": f"bu-{len(api.seen)}", "cdpUrl": "wss://bu.test/cdp", "timeoutAt": next(opened)}),
        ("PATCH", "/browsers/bu-1"): httpx.Response(200, json={}),
    })
    bu = BrowserUse("https://bu.test", "bu-key", client=api.client())

    first = await bu.env("s1", OPEN)
    again = await bu.env("s1", OPEN)
    other = await bu.env("s2", OPEN)  # its browser expired at once: the next call opens another
    expired = await bu.env("s2", OPEN)
    await bu.release("s1")

    assert first == again and other == expired
    assert [r.method for r in api.seen].count("POST") == 3
    assert api.seen[-1].method == "PATCH" and json.loads(api.seen[-1].content) == {"action": "stop"}
    assert all(r.headers["X-Browser-Use-API-Key"] == "bu-key" for r in api.seen)


async def test_firecrawl_opens_a_browser_for_its_ttl_and_deletes_it() -> None:
    api = Api({
        ("POST", "/v2/browser"): httpx.Response(200, json={"id": "fc-1", "cdpUrl": "wss://fc.test/cdp"}),
        ("DELETE", "/v2/browser/fc-1"): httpx.Response(204),
    })
    fc = Firecrawl("https://fc.test", "fc-key", ttl_s=600, client=api.client())

    env = await fc.env("s1", OPEN)
    await fc.release("s1")

    assert env["GITHI_BROWSER_CDP"] == "wss://fc.test/cdp"
    assert api.bodies() == [{"ttl": 600}]
    assert api.seen[0].headers["Authorization"] == "Bearer fc-key" and api.seen[-1].method == "DELETE"


async def test_lightpanda_is_the_sandboxs_own_engine() -> None:
    assert await SandboxLightpanda().env("s1", None) == {"GITHI_BROWSER_ENGINE": "lightpanda"}


async def test_each_hosted_provider_is_offered_when_its_credentials_are_set() -> None:
    configured = browser_providers_from_env({
        "BROWSERBASE_API_KEY": "k", "BROWSERBASE_PROJECT_ID": "p", "BROWSERBASE_PROXIES": "false", "BROWSERBASE_KEEP_ALIVE": "false",
        "BROWSERBASE_SESSION_TIMEOUT": "1800", "BROWSER_USE_API_KEY": "bu", "FIRECRAWL_API_KEY": "fc", "FIRECRAWL_BROWSER_TTL": "120",
        "BROWSER_INACTIVITY_TIMEOUT": "90",
    })
    bb, bu, fc = configured["browserbase"], configured["browser-use"], configured["firecrawl"]

    assert isinstance(bb, Browserbase) and (bb.proxies, bb.keep_alive, bb.timeout_s, bb.idle_s) == (False, False, 1800, 90)
    assert isinstance(bu, BrowserUse) and bu.api_url == "https://api.browser-use.com/api/v3"
    assert isinstance(fc, Firecrawl) and fc.ttl_s == 120 and fc.api_url == "https://api.firecrawl.dev"
    assert "browserbase" not in browser_providers_from_env({"BROWSERBASE_API_KEY": "k"}), "a project is needed too"
