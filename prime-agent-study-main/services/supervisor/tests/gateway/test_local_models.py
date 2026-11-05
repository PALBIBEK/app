"""A local server's models offered as routes: read from its model list, cached, never priced."""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from supervisor.gateway.app import app
from supervisor.gateway.local_models import FAILURE_TTL_S, TTL_S, LocalModels
from supervisor.gateway.vendors import VENDORS

TOKEN = "svc-token-for-local-model-tests"
SVC = {"Authorization": f"Bearer {TOKEN}"}
BASE = "http://localhost:11434/v1"


class _Server:
    """A local server's ``GET /models``: its answer can change, and it can go down."""

    def __init__(self, models: list[dict[str, Any]]) -> None:
        self.models = models
        self.down = False
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.down:
            raise httpx.ConnectError("refused", request=request)
        return httpx.Response(200, json={"object": "list", "data": self.models})


class _Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


def _local(server: _Server, clock: _Clock | None = None, key: str = "") -> LocalModels:
    return LocalModels(
        BASE,
        lambda: key,
        context_window=8192,
        transport=httpx.MockTransport(server),
        now=clock or _Clock(),
    )


def test_each_listed_model_is_a_free_local_route() -> None:
    routes = _local(_Server([{"id": "qwen3:8b"}, {"id": "llama3.2"}])).routes()
    assert list(routes) == ["local/qwen3:8b", "local/llama3.2"]
    route = routes["local/qwen3:8b"]
    assert (route.provider, route.upstream_model, route.provider_labels) == (
        "local",
        "qwen3:8b",
        ("local",),
    )
    assert (route.context_window, route.max_output_tokens) == (8192, 2048)
    assert (route.input_cost_per_token, route.output_cost_per_token) == (0.0, 0.0)


def test_a_window_the_server_reports_wins() -> None:
    routes = _local(_Server([{"id": "Qwen/Qwen3-8B", "max_model_len": 32768}])).routes()
    assert routes["local/Qwen/Qwen3-8B"].context_window == 32768


def test_an_id_that_is_no_route_id_is_skipped() -> None:
    routes = _local(
        _Server([{"id": "ok"}, {"id": "has space"}, {"id": ""}, {"nope": 1}, "x"])
    ).routes()
    assert list(routes) == ["local/ok"]


def test_the_list_is_read_with_the_local_key_when_there_is_one() -> None:
    server = _Server([])
    _local(server).routes()
    _local(server, key="vllm-key").routes()
    assert "authorization" not in server.requests[0].headers
    assert server.requests[1].headers["authorization"] == "Bearer vllm-key"
    assert str(server.requests[0].url) == f"{BASE}/models"


def test_the_list_is_cached_and_reread_when_stale() -> None:
    server, clock = _Server([{"id": "a"}]), _Clock()
    local = _local(server, clock)
    assert list(local.routes()) == ["local/a"]
    server.models = [{"id": "a"}, {"id": "b"}]
    clock.t += TTL_S - 1
    assert list(local.routes()) == ["local/a"]
    clock.t += 1
    assert list(local.routes()) == ["local/a", "local/b"]
    assert len(server.requests) == 2


def test_a_server_that_is_down_offers_nothing_and_is_tried_again_soon() -> None:
    server, clock = _Server([{"id": "a"}]), _Clock()
    local = _local(server, clock)
    server.down = True
    assert local.routes() == {}
    server.down = False
    clock.t += FAILURE_TTL_S - 1
    assert local.routes() == {}
    clock.t += 1
    assert list(local.routes()) == ["local/a"]


def test_no_server_configured_is_never_read() -> None:
    server = _Server([{"id": "a"}])
    local = LocalModels("", lambda: "", context_window=4096, transport=httpx.MockTransport(server))
    assert local.routes() == {} and server.requests == []


# -- through the gateway -----------------------------------------------------------------


@pytest.fixture
def gateway(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    for vendor in VENDORS.values():
        monkeypatch.delenv(vendor.key_env, raising=False)
        monkeypatch.delenv(vendor.base_url_env, raising=False)
    for name in ("AUTH_REQUIRED", "ENV", "MODEL_GATEWAY_KEYS_FILE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("MODEL_GATEWAY_TOKEN", TOKEN)
    monkeypatch.setenv("LOCAL_MODEL_BASE_URL", BASE)
    monkeypatch.setenv(
        "MODEL_GATEWAY_ROUTES_JSON",
        json.dumps(
            {
                "echo": {"provider": "echo", "upstream_model": "echo"},
                "local/qwen3:8b": {
                    "provider": "local",
                    "upstream_model": "qwen3:8b",
                    "context_window": 40960,
                },
            }
        ),
    )
    with TestClient(app) as client:
        client.app.state.local_models = _local(_Server([{"id": "qwen3:8b"}, {"id": "llama3.2"}]))
        yield client


def test_the_local_servers_models_are_offered_and_a_configured_route_wins(
    gateway: TestClient,
) -> None:
    listed = [m["id"] for m in gateway.get("/v1/models", headers=SVC).json()["data"]]
    assert listed == ["echo", "local/qwen3:8b", "local/llama3.2"]
    info = {
        r["model_group"]: r for r in gateway.get("/model_group/info", headers=SVC).json()["data"]
    }
    assert info["local/qwen3:8b"]["max_input_tokens"] == 40960
    assert info["local/llama3.2"]["providers"] == ["local"]


def test_without_the_local_url_its_routes_are_not_offered(
    gateway: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("LOCAL_MODEL_BASE_URL")
    with TestClient(app) as client:
        listed = [m["id"] for m in client.get("/v1/models", headers=SVC).json()["data"]]
        routes = [r["id"] for r in client.get("/v1/routes", headers=SVC).json()["routes"]]
    assert listed == ["echo"]
    assert routes == ["echo", "local/qwen3:8b"]
