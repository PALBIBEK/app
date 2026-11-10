"""Every session's model calls share the router's one HTTP client (``ModelRouter
.shared_http_client``): a client per session built a TLS context each time (a quarter of a
worker's CPU under the memory load test, G1 of docs/memory-gaps.md) and pooled nothing."""

from __future__ import annotations

import threading

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
