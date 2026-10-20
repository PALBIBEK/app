"""D5 §6 A / T5 — real provider streaming must not 501 (mock httpx upstream)."""

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

SERVICE = "d5-stream-svc"

_ROUTES = {
    "echo": {"provider": "echo", "upstream_model": "echo"},
    "openai": {"provider": "openai", "upstream_model": "gpt-4o-mini"},
    "githi": {
        "provider": "openrouter",
        "upstream_model": "anthropic/claude-sonnet-4",
    },
}

_SSE = (
    b'data: {"id":"c1","object":"chat.completion.chunk",'
    b'"choices":[{"index":0,"delta":{"content":"Hi"},"finish_reason":null}]}\n\n'
    b'data: {"id":"c1","object":"chat.completion.chunk",'
    b'"choices":[{"index":0,"delta":{"content":"!"},"finish_reason":null}]}\n\n'
    b'data: {"id":"c1","object":"chat.completion.chunk",'
    b'"choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}\n\n'
    b"data: [DONE]\n\n"
)


def _configure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MODEL_GATEWAY_TOKEN", SERVICE)
    monkeypatch.setenv("ENV", "development")
    monkeypatch.delenv("AUTH_REQUIRED", raising=False)
    monkeypatch.setenv("MODEL_GATEWAY_ROUTES_JSON", json.dumps(_ROUTES))
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-openai")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    settings = Settings()
    app.state.settings = settings
    app.state.registry = build_provider_registry(settings)
    app.state.virtual_keys = MemoryVirtualKeyStore()


def _patched_client(transport: httpx.MockTransport):
    class _Client(httpx.Client):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            kwargs["transport"] = transport
            super().__init__(*args, **kwargs)

    return _Client


def _handler(request: httpx.Request) -> httpx.Response:
    body = json.loads(request.content.decode())
    assert body.get("stream") is True
    return httpx.Response(
        200,
        headers={"content-type": "text/event-stream"},
        content=_SSE,
    )


@pytest.mark.parametrize(
    ("route_id", "patch_target"),
    [
        ("openai", "supervisor.gateway.providers.openai_compat.httpx.Client"),
        ("githi", "supervisor.gateway.providers.openrouter.httpx.Client"),
    ],
)
def test_d5_real_provider_stream_must_not_501(
    monkeypatch: pytest.MonkeyPatch,
    route_id: str,
    patch_target: str,
) -> None:
    """Focus #3: openai/openrouter stream path returns SSE, never 501."""
    _configure(monkeypatch)
    transport = httpx.MockTransport(_handler)
    with (
        patch(patch_target, _patched_client(transport)),
        TestClient(app) as client,
    ):
        _configure(monkeypatch)
        with client.stream(
            "POST",
            "/v1/chat/completions",
            headers={"Authorization": f"Bearer {SERVICE}"},
            json={
                "model": route_id,
                "stream": True,
                "messages": [{"role": "user", "content": "stream?"}],
            },
        ) as resp:
            assert resp.status_code != 501, (
                f"{route_id} stream must not be unimplemented (501)"
            )
            assert resp.status_code == 200, resp.read().decode(errors="replace")
            ctype = resp.headers.get("content-type") or ""
            assert "text/event-stream" in ctype
            text = "".join(resp.iter_text())
    assert "data:" in text
    assert "[DONE]" in text
    assert "Hi" in text or "content" in text
