"""D5 §6 B / T6 — HttpModelGatewayClient.stream is real multi-delta SSE parse."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from supervisor.seams.providers.http_gateway import HttpModelGatewayClient
from supervisor.seams.types import ChatMessage


def _sse_body() -> bytes:
    chunks = [
        {
            "id": "c1",
            "object": "chat.completion.chunk",
            "created": 1,
            "model": "echo",
            "choices": [{"index": 0, "delta": {"content": "One"}, "finish_reason": None}],
        },
        {
            "id": "c1",
            "object": "chat.completion.chunk",
            "created": 1,
            "model": "echo",
            "choices": [{"index": 0, "delta": {"content": "Two"}, "finish_reason": None}],
        },
        {
            "id": "c1",
            "object": "chat.completion.chunk",
            "created": 1,
            "model": "echo",
            "choices": [{"index": 0, "delta": {"content": "Three"}, "finish_reason": None}],
        },
        {
            "id": "c1",
            "object": "chat.completion.chunk",
            "created": 1,
            "model": "echo",
            "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
        },
    ]
    parts = [f"data: {json.dumps(c)}\n\n".encode() for c in chunks]
    parts.append(b"data: [DONE]\n\n")
    return b"".join(parts)


def test_t6_supervisor_stream_client_multiple_deltas_not_one_fake() -> None:
    """T6: stream() yields multiple deltas from SSE — not complete-then-one-delta."""
    calls: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode()) if request.content else {}
        calls.append(body)
        if body.get("stream") is True:
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                content=_sse_body(),
            )
        # Non-stream fallback must not be the path D5 accepts for prod stream().
        return httpx.Response(
            200,
            json={
                "id": "c1",
                "object": "chat.completion",
                "created": 1,
                "model": "echo",
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": "OneTwoThree",
                        },
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            },
        )

    transport = httpx.MockTransport(handler)
    client = HttpModelGatewayClient(
        base_url="http://gateway.test",
        token="svc",
        default_model="echo",
    )
    client.close()
    client._client = httpx.Client(transport=transport, base_url="http://gateway.test")

    events = list(
        client.stream(
            [ChatMessage(role="user", content="hi")],
            [],
            model="echo",
        )
    )
    client.close()

    deltas = [e for e in events if e.type == "delta" and (e.delta or "").strip()]
    assert len(deltas) >= 2, (
        f"expected ≥2 stream deltas from SSE, got {len(deltas)} "
        f"(events={[e.type for e in events]}); "
        "complete-then-single-delta is not D5-compliant"
    )
    joined = "".join(e.delta for e in deltas)
    assert "One" in joined and "Two" in joined
    # Prod profile must request stream:true (not non-stream complete).
    assert any(c.get("stream") is True for c in calls), (
        "HttpModelGatewayClient.stream must POST stream:true to the gateway"
    )
    dones = [e for e in events if e.type == "done"]
    assert dones, "stream must end with a done event"


def test_http_tool_calls_mapped_onto_completion_for_githi() -> None:
    """§6 B: HTTP tool_calls visible to githi tool loop (not dropped by client)."""
    payload = {
        "id": "c-tools",
        "object": "chat.completion",
        "created": 1,
        "model": "githi",
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "call_abc",
                            "type": "function",
                            "function": {
                                "name": "bash",
                                "arguments": "{\"cmd\":\"echo hi\"}",
                            },
                        }
                    ],
                },
                "finish_reason": "tool_calls",
            }
        ],
        "usage": {
            "prompt_tokens": 2,
            "completion_tokens": 2,
            "total_tokens": 4,
            "cost_total": 0.002,
        },
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    transport = httpx.MockTransport(handler)
    client = HttpModelGatewayClient(base_url="http://gateway.test", token="svc")
    client.close()
    client._client = httpx.Client(transport=transport, base_url="http://gateway.test")
    completion = client.complete(
        [ChatMessage(role="user", content="run")],
        [{"type": "function", "function": {"name": "bash", "parameters": {}}}],
        model="githi",
    )
    client.close()

    # Githi looks at message.tool_calls or completion.tool_calls.
    msg_calls = getattr(completion.message, "tool_calls", None)
    top_calls = getattr(completion, "tool_calls", None)
    assert msg_calls or top_calls, (
        "HttpModelGatewayClient.complete must surface tool_calls for the githi loop"
    )
