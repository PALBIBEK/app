"""A8 — retry on transient 429 (at least once) + optional fallback_route."""

from __future__ import annotations

import json

import httpx
import pytest
from fastapi.testclient import TestClient

from model_gateway.app import app
from model_gateway.providers.openai_compat import OpenAICompatProvider


class _CountingTransport(httpx.BaseTransport):
    def __init__(self, fail_times: int = 1) -> None:
        self.calls = 0
        self.fail_times = fail_times

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        self.calls += 1
        if self.calls <= self.fail_times:
            return httpx.Response(429, json={"error": "rate_limited"})
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-ok",
                "object": "chat.completion",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "recovered"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 1,
                    "completion_tokens": 1,
                    "total_tokens": 2,
                    "cost": 0.01,
                },
            },
        )


def test_openai_retries_once_on_429(monkeypatch: pytest.MonkeyPatch) -> None:
    routes = json.dumps(
        {
            "openai": {
                "provider": "openai",
                "upstream_model": "gpt-4o-mini",
                "retries": 1,
            }
        }
    )
    monkeypatch.delenv("MODEL_GATEWAY_TOKEN", raising=False)
    monkeypatch.delenv("AUTH_REQUIRED", raising=False)
    monkeypatch.delenv("ENV", raising=False)
    monkeypatch.setenv("MODEL_GATEWAY_ROUTES_JSON", routes)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

    transport = _CountingTransport(fail_times=1)
    with TestClient(app) as c:
        c.app.state.registry._by_name["openai"] = OpenAICompatProvider(  # noqa: SLF001
            api_key="sk-test",
            base_url="https://api.openai.com/v1",
            transport=transport,
            retries=1,
        )
        r = c.post(
            "/v1/chat/completions",
            json={
                "model": "openai",
                "messages": [{"role": "user", "content": "hi"}],
            },
        )
    assert r.status_code == 200, r.text
    assert r.json()["choices"][0]["message"]["content"] == "recovered"
    assert transport.calls == 2


def test_openai_exhausts_retries_surfaces_upstream(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    routes = json.dumps(
        {"openai": {"provider": "openai", "upstream_model": "gpt-4o-mini", "retries": 1}}
    )
    monkeypatch.delenv("MODEL_GATEWAY_TOKEN", raising=False)
    monkeypatch.setenv("MODEL_GATEWAY_ROUTES_JSON", routes)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

    transport = _CountingTransport(fail_times=99)
    with TestClient(app) as c:
        c.app.state.registry._by_name["openai"] = OpenAICompatProvider(  # noqa: SLF001
            api_key="sk-test",
            base_url="https://api.openai.com/v1",
            transport=transport,
            retries=1,
        )
        r = c.post(
            "/v1/chat/completions",
            json={
                "model": "openai",
                "messages": [{"role": "user", "content": "hi"}],
            },
        )
    assert r.status_code == 502
    assert r.json()["detail"]["error"]["code"] == "UPSTREAM_ERROR"
    assert transport.calls == 2


def test_fallback_route_on_primary_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    routes = json.dumps(
        {
            "primary": {
                "provider": "openai",
                "upstream_model": "gpt-4o-mini",
                "retries": 0,
                "fallback_route_ids": ["backup"],
            },
            "backup": {"provider": "echo", "upstream_model": "echo"},
        }
    )
    monkeypatch.delenv("MODEL_GATEWAY_TOKEN", raising=False)
    monkeypatch.setenv("MODEL_GATEWAY_ROUTES_JSON", routes)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

    transport = _CountingTransport(fail_times=99)
    with TestClient(app) as c:
        c.app.state.registry._by_name["openai"] = OpenAICompatProvider(  # noqa: SLF001
            api_key="sk-test",
            base_url="https://api.openai.com/v1",
            transport=transport,
            retries=0,
        )
        r = c.post(
            "/v1/chat/completions",
            json={
                "model": "primary",
                "messages": [{"role": "user", "content": "fallback-me"}],
            },
        )
    assert r.status_code == 200, r.text
    assert "fallback-me" in r.json()["choices"][0]["message"]["content"]
    assert r.json()["model"] == "backup"
