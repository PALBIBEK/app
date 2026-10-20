"""D5 §6 A / T5 — real-provider streaming SSE + usage/cost metering (mock transport)."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import patch

import httpx
import pytest
from fastapi.testclient import TestClient

from supervisor.gateway.app import app

from .helpers import SERVICE_TOKEN, configure_app


def _auth() -> dict[str, str]:
    return {"Authorization": f"Bearer {SERVICE_TOKEN}"}


def _upstream_completion_with_cost() -> dict[str, Any]:
    return {
        "id": "chatcmpl-d5-meter",
        "object": "chat.completion",
        "created": 1_700_000_000,
        "model": "gpt-4o-mini",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": "metered reply"},
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": 11,
            "completion_tokens": 7,
            "total_tokens": 18,
            "cost": 0.0042,
            "cost_total": 0.0042,
        },
    }


def _sse_upstream_body() -> bytes:
    """OpenAI-compat SSE body a real provider would emit for stream=true."""
    chunks = [
        {
            "id": "chatcmpl-d5-stream",
            "object": "chat.completion.chunk",
            "created": 1_700_000_000,
            "model": "gpt-4o-mini",
            "choices": [
                {"index": 0, "delta": {"role": "assistant", "content": "Hel"}, "finish_reason": None}
            ],
        },
        {
            "id": "chatcmpl-d5-stream",
            "object": "chat.completion.chunk",
            "created": 1_700_000_000,
            "model": "gpt-4o-mini",
            "choices": [{"index": 0, "delta": {"content": "lo"}, "finish_reason": None}],
        },
        {
            "id": "chatcmpl-d5-stream",
            "object": "chat.completion.chunk",
            "created": 1_700_000_000,
            "model": "gpt-4o-mini",
            "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
        },
    ]
    parts = [f"data: {json.dumps(c)}\n\n".encode() for c in chunks]
    parts.append(b"data: [DONE]\n\n")
    return b"".join(parts)


def test_t5_openai_compat_stream_sse_with_mock_transport(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """T5 / §6 A: stream:true for openai provider → SSE chunks + [DONE] (mock upstream)."""
    configure_app(monkeypatch, openai_key="sk-test-openai", openrouter_key="")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/chat/completions")
        body = json.loads(request.content.decode())
        assert body.get("stream") is True
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=_sse_upstream_body(),
        )

    transport = httpx.MockTransport(handler)

    class _PatchedClient(httpx.Client):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            kwargs["transport"] = transport
            super().__init__(*args, **kwargs)

    with (
        patch("supervisor.gateway.providers.openai_compat.httpx.Client", _PatchedClient),
        TestClient(app) as client,
    ):
        configure_app(monkeypatch, openai_key="sk-test-openai", openrouter_key="")
        with client.stream(
            "POST",
            "/v1/chat/completions",
            headers=_auth(),
            json={
                "model": "openai-demo",
                "stream": True,
                "messages": [{"role": "user", "content": "hi"}],
            },
        ) as resp:
            assert resp.status_code == 200
            assert "text/event-stream" in (resp.headers.get("content-type") or "")
            text = "".join(resp.iter_text())

    assert "data:" in text
    assert "[DONE]" in text
    # At least one content-bearing delta survived the relay.
    assert ("Hel" in text) or ("Hello" in text) or ("content" in text)


def test_completions_include_usage_and_cost_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """§6 A / T8 upstream: completions expose usage + cost fields supervisor reads."""
    configure_app(monkeypatch, openai_key="sk-test-openai")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_upstream_completion_with_cost())

    transport = httpx.MockTransport(handler)

    class _PatchedClient(httpx.Client):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            kwargs["transport"] = transport
            super().__init__(*args, **kwargs)

    with (
        patch("supervisor.gateway.providers.openai_compat.httpx.Client", _PatchedClient),
        TestClient(app) as client,
    ):
        configure_app(monkeypatch, openai_key="sk-test-openai")
        r = client.post(
            "/v1/chat/completions",
            headers=_auth(),
            json={
                "model": "openai-demo",
                "stream": False,
                "messages": [{"role": "user", "content": "cost?"}],
            },
        )
    assert r.status_code == 200
    data = r.json()
    usage = data.get("usage") or {}
    assert isinstance(usage, dict)
    # Supervisor githi maps cost_total or cost (see loop._usage_from_completion).
    cost = usage.get("cost_total", usage.get("cost", data.get("cost_total", data.get("cost"))))
    assert cost is not None
    assert float(cost) > 0
    assert int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0) > 0
    assert int(usage.get("completion_tokens") or usage.get("output_tokens") or 0) > 0


def test_real_provider_non_stream_with_tools_when_key_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """§6 A: at least one real provider completes non-stream with tools when key present."""
    configure_app(monkeypatch, openai_key="sk-test-openai")

    tool_payload = {
        "id": "chatcmpl-tools",
        "object": "chat.completion",
        "created": 1,
        "model": "gpt-4o-mini",
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {
                                "name": "lookup",
                                "arguments": "{\"q\":\"x\"}",
                            },
                        }
                    ],
                },
                "finish_reason": "tool_calls",
            }
        ],
        "usage": {
            "prompt_tokens": 3,
            "completion_tokens": 2,
            "total_tokens": 5,
            "cost_total": 0.001,
        },
    }

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode())
        assert body.get("tools")
        return httpx.Response(200, json=tool_payload)

    transport = httpx.MockTransport(handler)

    class _PatchedClient(httpx.Client):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            kwargs["transport"] = transport
            super().__init__(*args, **kwargs)

    with (
        patch("supervisor.gateway.providers.openai_compat.httpx.Client", _PatchedClient),
        TestClient(app) as client,
    ):
        configure_app(monkeypatch, openai_key="sk-test-openai")
        r = client.post(
            "/v1/chat/completions",
            headers=_auth(),
            json={
                "model": "openai-demo",
                "messages": [{"role": "user", "content": "use tool"}],
                "tools": [
                    {
                        "type": "function",
                        "function": {
                            "name": "lookup",
                            "parameters": {"type": "object", "properties": {}},
                        },
                    }
                ],
            },
        )
    assert r.status_code == 200
    msg = r.json()["choices"][0]["message"]
    assert msg.get("tool_calls")


def test_echo_stream_still_sse_shaped(
    client: TestClient, auth_header: dict[str, str]
) -> None:
    """Baseline: echo stream remains OpenAI-compat SSE (regression guard)."""
    with client.stream(
        "POST",
        "/v1/chat/completions",
        headers=auth_header,
        json={
            "model": "echo",
            "stream": True,
            "messages": [{"role": "user", "content": "s"}],
        },
    ) as resp:
        assert resp.status_code == 200
        text = "".join(resp.iter_text())
    assert "data:" in text
    assert "[DONE]" in text
