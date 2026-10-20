"""D5 §6 A / T8 — completions expose usage + cost fields supervisor reads."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import patch

import httpx
import pytest
from fastapi.testclient import TestClient

from supervisor.gateway.app import app
from supervisor.gateway.config import Settings
from supervisor.gateway.factory import build_provider_registry
from supervisor.gateway.virtual_keys import MemoryVirtualKeyStore

SERVICE = "d5-meter-svc"

_ROUTES = {
    "echo": {"provider": "echo", "upstream_model": "echo"},
    "openai": {"provider": "openai", "upstream_model": "gpt-4o-mini"},
}


def _configure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MODEL_GATEWAY_TOKEN", SERVICE)
    monkeypatch.setenv("ENV", "development")
    monkeypatch.delenv("AUTH_REQUIRED", raising=False)
    monkeypatch.setenv("MODEL_GATEWAY_ROUTES_JSON", json.dumps(_ROUTES))
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-openai")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    settings = Settings()
    app.state.settings = settings
    app.state.registry = build_provider_registry(settings)
    app.state.virtual_keys = MemoryVirtualKeyStore()


def test_d5_completions_include_usage_and_cost_for_supervisor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Focus #4: usage + cost_total/cost present for supervisor budget mapping."""
    _configure(monkeypatch)

    upstream = {
        "id": "chatcmpl-meter",
        "object": "chat.completion",
        "created": 1,
        "model": "gpt-4o-mini",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": "ok"},
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": 9,
            "completion_tokens": 3,
            "total_tokens": 12,
            "cost": 0.0025,
        },
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=upstream)

    transport = httpx.MockTransport(handler)

    class _Client(httpx.Client):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            kwargs["transport"] = transport
            super().__init__(*args, **kwargs)

    with (
        patch("supervisor.gateway.providers.openai_compat.httpx.Client", _Client),
        TestClient(app) as client,
    ):
        _configure(monkeypatch)
        r = client.post(
            "/v1/chat/completions",
            headers={"Authorization": f"Bearer {SERVICE}"},
            json={
                "model": "openai",
                "stream": False,
                "messages": [{"role": "user", "content": "cost"}],
            },
        )
    assert r.status_code == 200, r.text
    data = r.json()
    usage = data.get("usage")
    assert isinstance(usage, dict), data
    prompt = usage.get("prompt_tokens") or usage.get("input_tokens")
    completion = usage.get("completion_tokens") or usage.get("output_tokens")
    assert int(prompt or 0) > 0
    assert int(completion or 0) > 0
    cost = usage.get("cost_total", usage.get("cost", data.get("cost_total", data.get("cost"))))
    assert cost is not None, f"missing cost fields supervisor reads: {data}"
    assert float(cost) > 0


def test_d5_echo_completion_also_exposes_nonzero_or_structured_usage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Echo path must still return a usage object (even if estimated)."""
    _configure(monkeypatch)
    with TestClient(app) as client:
        _configure(monkeypatch)
        r = client.post(
            "/v1/chat/completions",
            headers={"Authorization": f"Bearer {SERVICE}"},
            json={
                "model": "echo",
                "messages": [{"role": "user", "content": "hello"}],
            },
        )
    assert r.status_code == 200, r.text
    usage = r.json().get("usage")
    assert isinstance(usage, dict)
    # cost may be estimated; require the key surface supervisor already reads.
    assert (
        "cost_total" in usage
        or "cost" in usage
        or "prompt_tokens" in usage
        or "input_tokens" in usage
    ), usage
