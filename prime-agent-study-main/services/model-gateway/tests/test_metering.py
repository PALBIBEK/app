"""A5 — completions normalize usage + cost / cost_total for supervisor."""

from __future__ import annotations

import json

import httpx
import pytest
from fastapi.testclient import TestClient

from model_gateway.app import app
from model_gateway.metering import normalize_usage
from model_gateway.providers.openai_compat import OpenAICompatProvider


def test_normalize_usage_mirrors_and_estimates() -> None:
    out = normalize_usage({"prompt_tokens": 100, "completion_tokens": 50})
    assert out["prompt_tokens"] == 100
    assert out["input_tokens"] == 100
    assert out["completion_tokens"] == 50
    assert out["output_tokens"] == 50
    assert out["cost"] == out["cost_total"]
    assert out["cost_total"] > 0


def test_normalize_usage_prefers_upstream_cost() -> None:
    out = normalize_usage(
        {"prompt_tokens": 1, "completion_tokens": 1, "cost": 0.42},
        invent_cost=True,
    )
    assert out["cost"] == 0.42
    assert out["cost_total"] == 0.42


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.delenv("MODEL_GATEWAY_TOKEN", raising=False)
    monkeypatch.delenv("AUTH_REQUIRED", raising=False)
    monkeypatch.delenv("ENV", raising=False)
    monkeypatch.delenv("MODEL_GATEWAY_ROUTES_JSON", raising=False)
    with TestClient(app) as c:
        yield c


def test_echo_completion_includes_cost(client: TestClient) -> None:
    r = client.post(
        "/v1/chat/completions",
        json={"model": "echo", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert r.status_code == 200
    usage = r.json()["usage"]
    assert "prompt_tokens" in usage
    assert "completion_tokens" in usage
    assert usage["cost"] == usage["cost_total"]
    assert float(usage["cost_total"]) > 0


def test_openai_completion_normalizes_upstream_usage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    routes = json.dumps(
        {"openai": {"provider": "openai", "upstream_model": "gpt-4o-mini"}}
    )
    monkeypatch.delenv("MODEL_GATEWAY_TOKEN", raising=False)
    monkeypatch.delenv("AUTH_REQUIRED", raising=False)
    monkeypatch.delenv("ENV", raising=False)
    monkeypatch.setenv("MODEL_GATEWAY_ROUTES_JSON", routes)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode())
        assert body.get("stream") is False
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-x",
                "object": "chat.completion",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "ok"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 10,
                    "completion_tokens": 5,
                    "total_tokens": 15,
                },
            },
        )

    transport = httpx.MockTransport(handler)
    with TestClient(app) as c:
        c.app.state.registry._by_name["openai"] = OpenAICompatProvider(  # noqa: SLF001
            api_key="sk-test",
            base_url="https://api.openai.com/v1",
            transport=transport,
        )
        r = c.post(
            "/v1/chat/completions",
            json={
                "model": "openai",
                "messages": [{"role": "user", "content": "hi"}],
            },
        )
    assert r.status_code == 200, r.text
    usage = r.json()["usage"]
    assert usage["prompt_tokens"] == 10
    assert usage["completion_tokens"] == 5
    assert usage["cost"] == usage["cost_total"]
    assert float(usage["cost_total"]) > 0
