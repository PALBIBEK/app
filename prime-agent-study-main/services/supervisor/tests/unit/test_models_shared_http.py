"""Every session's model calls share the router's one HTTP client (``ModelRouter
.shared_http_client``): a client per session built a TLS context each time (a quarter of a
worker's CPU under the memory load test, G1 of docs/memory-gaps.md) and pooled nothing."""

from __future__ import annotations

import threading

import pytest

from supervisor.models.client import ModelRouter, RoutedModelClient
from supervisor.models.endpoints import GatewayConfig

CONFIG = GatewayConfig(url="https://gateway.example/v1", api_key="k", api_key_header="x-gateway-key")


def test_sessions_share_one_client_and_closing_one_leaves_it_open() -> None:
    router = ModelRouter(CONFIG)
    first, second = RoutedModelClient(router, session_id="s1"), RoutedModelClient(router, session_id="s2")

    shared = first._client()
    first.close()

    assert second._client() is shared and not shared.is_closed, "one client, still open for the others"


def test_threads_asking_at_once_get_the_same_client() -> None:
    router = ModelRouter(CONFIG)
    got: list[object] = []
    gate = threading.Barrier(16)

    def ask() -> None:
        gate.wait()
        got.append(router.shared_http_client())

    threads = [threading.Thread(target=ask) for _ in range(16)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len({id(c) for c in got}) == 1


def test_a_client_of_the_callers_own_is_still_new() -> None:
    router = ModelRouter(CONFIG)
    with router.http_client() as own:
        assert own is not router.shared_http_client()


def test_closing_the_router_closes_its_shared_client() -> None:
    router = ModelRouter(CONFIG)
    shared = router.shared_http_client()

    router.close()

    assert shared.is_closed and router.shared_http_client() is not shared


def test_the_shared_client_keeps_no_cookie_from_one_session_for_another() -> None:
    import httpx

    sent: list[str | None] = []

    def handle(request: httpx.Request) -> httpx.Response:
        sent.append(request.headers.get("cookie"))
        return httpx.Response(200, headers={"set-cookie": "sticky=backend-3; Path=/"})

    router = ModelRouter(CONFIG, transport=httpx.MockTransport(handle))
    for _ in range(2):
        router.shared_http_client().get("https://gateway.example/v1/models")

    assert sent == [None, None]


async def test_the_memory_client_keeps_no_cookie_from_one_tenant_for_another() -> None:
    import httpx

    from supervisor.memory.client import NotebookClient

    sent: list[str | None] = []

    async def handle(request: httpx.Request) -> httpx.Response:
        sent.append(request.headers.get("cookie"))
        return httpx.Response(200, json={"items": []}, headers={"set-cookie": "sticky=replica-2; Path=/"})

    client = NotebookClient("http://memory.test", transport=httpx.MockTransport(handle))
    for _ in range(2):
        await client._http.get("/v1/notebooks")
    await client.aclose()

    assert sent == [None, None]


async def test_a_rate_limited_memory_request_is_asked_again() -> None:
    """Refused before it ran, so safe to ask again whatever it does: a burst of one
    tenant's sessions waits its turn instead of failing (the load test's one error)."""
    import httpx

    from supervisor.memory.client import NotebookClient

    answers = [429, 429, 200]

    async def handle(request: httpx.Request) -> httpx.Response:
        status = answers.pop(0)
        if status == 429:
            return httpx.Response(429, json={"detail": {"code": "RATE_LIMITED", "message": "slow down"}},
                                  headers={"Retry-After": "0.01"})
        return httpx.Response(200, json={"added": 1})

    client = NotebookClient("http://memory.test", transport=httpx.MockTransport(handle))
    try:
        assert await client.capture("t1~abc", ["x"]) == 1
    finally:
        await client.aclose()
    assert answers == []


async def test_a_rate_limited_request_waits_the_time_asked(monkeypatch: object) -> None:
    import httpx

    from supervisor.memory import client as memory_client
    from supervisor.memory.client import NotebookClient

    waits: list[float] = []

    async def sleep(seconds: float) -> None:
        waits.append(seconds)

    monkeypatch.setattr(memory_client.asyncio, "sleep", sleep)  # type: ignore[attr-defined]
    answers = [429, 200]

    async def handle(request: httpx.Request) -> httpx.Response:
        if answers.pop(0) == 429:
            return httpx.Response(429, json={"detail": {"code": "RATE_LIMITED", "message": "slow"}},
                                  headers={"Retry-After": "3"})
        return httpx.Response(200, json={"added": 1})

    client = NotebookClient("http://memory.test", transport=httpx.MockTransport(handle))
    try:
        await client.capture("t1~abc", ["x"])
    finally:
        await client.aclose()
    (waited,) = waits
    assert 3.0 <= waited <= memory_client.MAX_RATE_WAIT_S, "never sooner than asked, never past the cap"


async def test_a_wait_longer_than_ours_is_not_retried_inside_its_window() -> None:
    import httpx

    from supervisor.memory.client import NotebookClient, RateLimited

    calls = 0

    async def handle(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(429, json={"detail": {"code": "RATE_LIMITED", "message": "slow"}},
                              headers={"Retry-After": "30"})

    client = NotebookClient("http://memory.test", transport=httpx.MockTransport(handle))
    try:
        with pytest.raises(RateLimited):
            await client.capture("t1~abc", ["x"])
    finally:
        await client.aclose()
    assert calls == 1
