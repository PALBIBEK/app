"""HttpModelGatewayClient unit tests (mocked HTTP — no live gateway required)."""

from __future__ import annotations

import httpx
import pytest

from supervisor.seams.factory import build_runtime_seams
from supervisor.seams.providers.fake_gateway import FakeModelGatewayClient
from supervisor.seams.providers.http_gateway import HttpModelGatewayClient
from supervisor.seams.types import ChatMessage


def test_mg_s1_http_client_maps_completion(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/v1/chat/completions")
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-test",
                "model": "echo",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "echo:hi"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            },
        )

    transport = httpx.MockTransport(handler)
    client = HttpModelGatewayClient(base_url="http://gateway.test", token="t")
    client._client = httpx.Client(transport=transport)
    try:
        out = client.complete(
            [ChatMessage(role="user", content="hi")],
            [],
            model="echo",
        )
        assert out.message.content == "echo:hi"
        assert out.model == "echo"
        assert out.finish_reason == "stop"
    finally:
        client.close()


def test_mg_s2_factory_http_when_base_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GATEWAY_BASE_URL", raising=False)
    seams = build_runtime_seams(gateway_base_url="http://127.0.0.1:8090")
    assert isinstance(seams.model_gateway, HttpModelGatewayClient)
    seams.model_gateway.close()


def test_mg_s2b_factory_fake_when_no_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GATEWAY_BASE_URL", raising=False)
    seams = build_runtime_seams()
    assert isinstance(seams.model_gateway, FakeModelGatewayClient)


def test_mg_s1_http_client_loud_on_4xx() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            404,
            json={"detail": {"error": {"code": "ROUTE_UNKNOWN", "message": "nope"}}},
        )

    client = HttpModelGatewayClient(base_url="http://gateway.test")
    client._client = httpx.Client(transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(RuntimeError, match="model gateway error 404"):
            client.complete([ChatMessage(role="user", content="x")], [], model="missing")
    finally:
        client.close()
