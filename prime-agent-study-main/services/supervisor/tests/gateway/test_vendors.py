"""The vendors the gateway calls: each one's endpoint and key, and what it needs to be ready."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from supervisor.gateway.app import app
from supervisor.gateway.config import Settings
from supervisor.gateway.factory import build_provider_registry, vendor_provider
from supervisor.gateway.providers.base import Call, ProviderUnconfigured, Wire
from supervisor.gateway.vendors import VENDORS, parse_base_url

TOKEN = "svc-token"
SVC = {"Authorization": f"Bearer {TOKEN}"}
_VENDOR_ENV = [name for v in VENDORS.values() for name in (v.key_env, v.base_url_env)]


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (*_VENDOR_ENV, "AUTH_REQUIRED", "ENV", "MODEL_GATEWAY_ROUTES_JSON"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("MODEL_GATEWAY_TOKEN", TOKEN)


def _settings(monkeypatch: pytest.MonkeyPatch, **env: str) -> Settings:
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    return Settings()


class _Upstream:
    """Records each request and answers like an OpenAI-compatible server."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return httpx.Response(
            200, json={"choices": [], "usage": {"prompt_tokens": 1, "completion_tokens": 1}}
        )


def _chat(vendor_id: str, settings: Settings) -> tuple[httpx.Request, Any]:
    upstream = _Upstream()
    provider = vendor_provider(
        VENDORS[vendor_id], settings, transport=httpx.MockTransport(upstream)
    )
    body = provider.call(
        Call(Wire.CHAT, {"model": "m", "messages": [{"role": "user", "content": "hi"}]})
    )
    return upstream.requests[0], body


# -- base URLs ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "normalized"),
    [
        ("https://api.deepseek.com/", "https://api.deepseek.com"),
        ("  http://localhost:11434/v1  ", "http://localhost:11434/v1"),
        ("", ""),
    ],
)
def test_a_base_url_is_normalized(value: str, normalized: str) -> None:
    assert parse_base_url("X_BASE_URL", value) == normalized


@pytest.mark.parametrize(
    ("value", "problem"),
    [
        ("ftp://example.com", "http(s) URL"),
        ("localhost:11434", "http(s) URL"),
        ("https://user:pw@example.com/v1", "credentials"),
        ("https://example.com/v1?key=1", "query string"),
        ("https://example.com/v1#x", "fragment"),
        ("http://localhost:99999/v1", "not a valid URL"),
    ],
)
def test_a_base_url_that_would_misroute_is_refused(value: str, problem: str) -> None:
    with pytest.raises(ValueError, match="X_BASE_URL") as raised:
        parse_base_url("X_BASE_URL", value)
    assert problem in str(raised.value)


def test_the_gateway_does_not_start_on_a_bad_base_url(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(ValueError, match="DEEPSEEK_BASE_URL must not contain credentials"):
        _settings(monkeypatch, DEEPSEEK_BASE_URL="https://k:s@api.deepseek.com")


# -- providers ---------------------------------------------------------------------------


def test_every_vendor_has_a_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    registry = build_provider_registry(_settings(monkeypatch))
    assert set(VENDORS) <= set(registry)
    assert registry["openai"].wires == {Wire.CHAT, Wire.RESPONSES}
    for vendor in ("deepseek", "zai", "qwen", "local", "openrouter"):
        assert registry[vendor].wires == {Wire.CHAT}, vendor


@pytest.mark.parametrize(
    ("vendor_id", "env", "url"),
    [
        ("deepseek", {"DEEPSEEK_API_KEY": "sk-d"}, "https://api.deepseek.com/chat/completions"),
        ("zai", {"ZAI_API_KEY": "sk-d"}, "https://api.z.ai/api/paas/v4/chat/completions"),
        (
            "qwen",
            {"DASHSCOPE_API_KEY": "sk-d"},
            "https://dashscope-intl.aliyuncs.com/compatible-mode/v1/chat/completions",
        ),
        (
            "qwen",
            {
                "DASHSCOPE_API_KEY": "sk-d",
                "DASHSCOPE_BASE_URL": "https://dashscope.aliyuncs.com/compatible-mode/v1",
            },
            "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
        ),
    ],
)
def test_a_vendor_is_called_at_its_endpoint_with_its_key(
    monkeypatch: pytest.MonkeyPatch, vendor_id: str, env: dict[str, str], url: str
) -> None:
    request, _ = _chat(vendor_id, _settings(monkeypatch, **env))
    assert str(request.url) == url
    assert request.headers["authorization"] == "Bearer sk-d"


def test_a_vendor_without_its_key_is_not_called(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = vendor_provider(VENDORS["deepseek"], _settings(monkeypatch))
    assert not provider.credential_ok()
    with pytest.raises(ProviderUnconfigured, match="DEEPSEEK_API_KEY is not configured"):
        provider.call(Call(Wire.CHAT, {"model": "m", "messages": []}))


def test_the_local_vendor_is_off_until_its_url_is_set(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = vendor_provider(VENDORS["local"], _settings(monkeypatch))
    assert not provider.credential_ok()
    with pytest.raises(ProviderUnconfigured, match="LOCAL_MODEL_BASE_URL is not configured"):
        provider.call(Call(Wire.CHAT, {"model": "m", "messages": []}))


def test_a_local_server_is_called_without_a_key(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings(monkeypatch, LOCAL_MODEL_BASE_URL="http://localhost:11434/v1")
    assert vendor_provider(VENDORS["local"], settings).credential_ok()
    request, _ = _chat("local", settings)
    assert str(request.url) == "http://localhost:11434/v1/chat/completions"
    assert "authorization" not in request.headers


def test_a_local_server_started_with_a_key_gets_it(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings(
        monkeypatch, LOCAL_MODEL_BASE_URL="http://127.0.0.1:8000/v1", LOCAL_MODEL_API_KEY="vllm-key"
    )
    request, _ = _chat("local", settings)
    assert request.headers["authorization"] == "Bearer vllm-key"


# -- routes and readiness ----------------------------------------------------------------


def _client(monkeypatch: pytest.MonkeyPatch, routes: dict[str, Any], **env: str) -> TestClient:
    monkeypatch.setenv("MODEL_GATEWAY_ROUTES_JSON", json.dumps(routes))
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    return TestClient(app)


_ECHO = {"echo": {"provider": "echo", "upstream_model": "echo"}}


@pytest.mark.parametrize(
    ("env", "route"),
    [
        ("DEEPSEEK_API_KEY", "deepseek-flash"),
        ("ZAI_API_KEY", "glm-5.2"),
        ("DASHSCOPE_API_KEY", "qwen3.7-plus"),
    ],
)
def test_a_vendor_key_unlocks_its_priced_default_route(
    monkeypatch: pytest.MonkeyPatch, env: str, route: str
) -> None:
    with _client(monkeypatch, _ECHO, **{env: "k"}) as c:
        info = {m["model_group"]: m for m in c.get("/model_group/info", headers=SVC).json()["data"]}
        assert c.get("/v1/readyz").json() == {"status": "ready"}
    assert list(info) == ["echo", route]
    group = info[route]
    assert group["input_cost_per_token"] and group["output_cost_per_token"]
    assert group["max_output_tokens"] < group["max_input_tokens"]
    assert group["supports_function_calling"] is True


def test_a_local_route_is_ready_only_with_the_local_url(monkeypatch: pytest.MonkeyPatch) -> None:
    routes = {**_ECHO, "qwen3-local": {"provider": "local", "upstream_model": "qwen3:8b"}}
    with _client(monkeypatch, routes) as c:
        assert c.get("/v1/readyz").json() == {"status": "not_ready", "reason": "credentials"}
    with _client(monkeypatch, routes, LOCAL_MODEL_BASE_URL="http://localhost:11434/v1") as c:
        assert c.get("/v1/readyz").json() == {"status": "ready"}
