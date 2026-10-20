"""A4 — real-provider stream: true SSE via mock httpx transport (not 501)."""

from __future__ import annotations

import json

import httpx
import pytest
from fastapi.testclient import TestClient

from supervisor.gateway.app import app
from supervisor.gateway.providers.openai_compat import OpenAICompatProvider
from supervisor.gateway.providers.openrouter import OpenRouterChatProvider

_OPENAI_ROUTES = json.dumps(
    {
        "echo": {"provider": "echo", "upstream_model": "echo"},
        "openai": {"provider": "openai", "upstream_model": "gpt-4o-mini"},
        "openrouter": {
            "provider": "openrouter",
            "upstream_model": "anthropic/claude-sonnet-4",
        },
    }
)

_SSE_BODY = (
    b'data: {"id":"chatcmpl-1","object":"chat.completion.chunk",'
    b'"choices":[{"index":0,"delta":{"content":"hel"},"finish_reason":null}]}\n\n'
    b'data: {"id":"chatcmpl-1","object":"chat.completion.chunk",'
    b'"choices":[{"index":0,"delta":{"content":"lo"},"finish_reason":null}]}\n\n'
    b'data: {"id":"chatcmpl-1","object":"chat.completion.chunk",'
    b'"choices":[{"index":0,"delta":{},"finish_reason":"stop"}],'
    b'"usage":{"prompt_tokens":2,"completion_tokens":2,"total_tokens":4,"cost":0.002}}\n\n'
    b"data: [DONE]\n\n"
)


def _stream_handler(request: httpx.Request) -> httpx.Response:
    body = json.loads(request.content.decode())
    assert body.get("stream") is True
    return httpx.Response(
        200,
        content=_SSE_BODY,
        headers={"content-type": "text/event-stream"},
    )


@pytest.fixture()
def openai_stream_client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.delenv("MODEL_GATEWAY_TOKEN", raising=False)
    monkeypatch.delenv("AUTH_REQUIRED", raising=False)
    monkeypatch.delenv("ENV", raising=False)
    monkeypatch.setenv("MODEL_GATEWAY_ROUTES_JSON", _OPENAI_ROUTES)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-test")
    transport = httpx.MockTransport(_stream_handler)
    with TestClient(app) as c:
        c.app.state.registry._by_name["openai"] = OpenAICompatProvider(
            api_key="sk-test",
            base_url="https://api.openai.com/v1",
            transport=transport,
        )
        c.app.state.registry._by_name["openrouter"] = OpenRouterChatProvider(
            api_key="or-test",
            transport=transport,
        )
        yield c


def test_openai_stream_sse_not_501(openai_stream_client: TestClient) -> None:
    r = openai_stream_client.post(
        "/v1/chat/completions",
        json={
            "model": "openai",
            "stream": True,
            "messages": [{"role": "user", "content": "hi"}],
        },
    )
    assert r.status_code == 200, r.text
    assert "text/event-stream" in r.headers.get("content-type", "")
    text = r.text
    assert "data:" in text
    assert "[DONE]" in text
    assert "hel" in text
    assert "lo" in text


def test_openrouter_stream_sse_not_501(openai_stream_client: TestClient) -> None:
    r = openai_stream_client.post(
        "/v1/chat/completions",
        json={
            "model": "openrouter",
            "stream": True,
            "messages": [{"role": "user", "content": "hi"}],
        },
    )
    assert r.status_code == 200, r.text
    assert "text/event-stream" in r.headers.get("content-type", "")
    assert "data:" in r.text
    assert "[DONE]" in r.text


def test_echo_stream_still_works(openai_stream_client: TestClient) -> None:
    r = openai_stream_client.post(
        "/v1/chat/completions",
        json={
            "model": "echo",
            "stream": True,
            "messages": [{"role": "user", "content": "ping"}],
        },
    )
    assert r.status_code == 200
    assert "echo:ping" in r.text or "ping" in r.text
