"""HttpModelGatewayClient as a ModelCatalog: the context window behind a route."""

from __future__ import annotations

import httpx
import pytest

from supervisor.seams import ModelCatalog
from supervisor.seams.providers.gateway.fake_gateway import FakeModelGatewayClient
from supervisor.seams.providers.gateway.http_gateway import HttpModelGatewayClient


def _client(transport: httpx.MockTransport) -> HttpModelGatewayClient:
    gateway = HttpModelGatewayClient(base_url="http://gw", token="vk_session")
    gateway._client = httpx.Client(transport=transport)
    return gateway


def test_gateway_clients_are_model_catalogs() -> None:
    assert isinstance(HttpModelGatewayClient(base_url="http://gw"), ModelCatalog)
    assert FakeModelGatewayClient(context_window=8_000).context_window("any") == 8_000


def test_a_routes_window_is_asked_once_with_the_session_key() -> None:
    seen: list[httpx.Request] = []

    def route(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"id": "githi", "context_window": 200_000})

    gateway = _client(httpx.MockTransport(route))
    assert gateway.context_window("githi") == 200_000
    assert gateway.context_window("githi") == 200_000
    assert [r.url.path for r in seen] == ["/v1/routes/githi"]
    assert seen[0].headers["authorization"] == "Bearer vk_session"


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(404, json={"detail": {"error": {"code": "ROUTE_UNKNOWN"}}}),
        httpx.Response(200, json={"id": "x", "context_window": None}),
        httpx.Response(200, json={"id": "x", "context_window": "big"}),
    ],
    ids=["unknown-route", "unreported", "malformed"],
)
def test_a_window_the_gateway_cannot_give_is_none(response: httpx.Response) -> None:
    gateway = _client(httpx.MockTransport(lambda request: response))
    assert gateway.context_window("x") is None


def test_an_unreachable_gateway_is_asked_again_later() -> None:
    calls = 0

    def flaky(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise httpx.ConnectError("down", request=request)
        return httpx.Response(200, json={"id": "x", "context_window": 32_000})

    gateway = _client(httpx.MockTransport(flaky))
    assert gateway.context_window("x") is None
    assert gateway.context_window("x") == 32_000
