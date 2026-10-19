"""The LiteLLM contract a QM-style client discovers and calls.

Discovery (``/v1/models`` + ``/model_group/info``), the three inference wires,
streaming on each, per-route pricing, the configurable key header, and the
replay strictness echo shares with the real vendors.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from model_gateway.app import COST_HEADER, app
from model_gateway.providers.anthropic import AnthropicProvider
from model_gateway.providers.openai_compat import OpenAICompatProvider
from model_gateway.wire_codec import iter_sse_data

TOKEN = "svc-token"
SVC = {"Authorization": f"Bearer {TOKEN}"}

ROUTES = {
    "echo": {
        "provider": "echo",
        "upstream_model": "echo",
        "context_window": 1000,
        "max_output_tokens": 100,
        "input_cost_per_token": 0.001,
        "output_cost_per_token": 0.002,
    },
    "claude": {
        "provider": "echo",
        "upstream_model": "claude-echo",
        "providers": ["anthropic"],
        "context_window": 1000,
        "max_output_tokens": 100,
        "input_cost_per_token": 0.001,
        "output_cost_per_token": 0.001,
        "supports_reasoning": True,
        "supports_adaptive_thinking": True,
    },
    "gpt": {
        "provider": "echo",
        "upstream_model": "gpt-echo",
        "providers": ["openai"],
        "context_window": 1000,
        "max_output_tokens": 100,
        "input_cost_per_token": 0.001,
        "output_cost_per_token": 0.001,
        "supports_reasoning": True,
        "supported_openai_params": ["reasoning_effort", "max_completion_tokens"],
    },
    "router": {"provider": "openrouter", "upstream_model": "x/y"},
    "embed": {"provider": "echo", "upstream_model": "e", "mode": "embedding"},
}


def _client(
    monkeypatch: pytest.MonkeyPatch, *, routes: dict[str, Any] = ROUTES, **env: str
) -> TestClient:
    for name in (
        "AUTH_REQUIRED",
        "ENV",
        "OPENAI_API_KEY",
        "OPENROUTER_API_KEY",
        "ANTHROPIC_API_KEY",
        "MODEL_GATEWAY_API_KEY_HEADER",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("MODEL_GATEWAY_TOKEN", TOKEN)
    monkeypatch.setenv("MODEL_GATEWAY_ROUTES_JSON", json.dumps(routes))
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    return TestClient(app)


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    with _client(monkeypatch) as c:
        yield c


def _vk(client: TestClient, *models: str, budget: float = 5.0) -> dict[str, str]:
    minted = client.post(
        "/v1/virtual-keys",
        headers=SVC,
        json={
            "tenant_id": "t",
            "session_id": "s",
            "principal_id": "p",
            "allowed_models": list(models),
            "max_budget_usd": budget,
        },
    )
    assert minted.status_code == 200, minted.text
    return {"Authorization": f"Bearer {minted.json()['token']}"}


def _events(text: str) -> list[dict[str, Any]]:
    return [json.loads(d) for d in iter_sse_data([text.encode()]) if d != "[DONE]"]


# -- discovery -------------------------------------------------------------


def test_model_list_and_group_info_describe_every_route(client: TestClient) -> None:
    listed = client.get("/v1/models", headers=SVC).json()
    assert listed["object"] == "list"
    assert [m["id"] for m in listed["data"]] == list(ROUTES)

    info = {
        row["model_group"]: row
        for row in client.get("/model_group/info", headers=SVC).json()["data"]
    }
    assert info["claude"] == {
        "model_group": "claude",
        "mode": "chat",
        "providers": ["anthropic"],
        "max_input_tokens": 1000,
        "max_output_tokens": 100,
        "input_cost_per_token": 0.001,
        "output_cost_per_token": 0.001,
        "cache_read_input_token_cost": None,
        "cache_creation_input_token_cost": None,
        "supports_function_calling": True,
        "supports_vision": False,
        "supports_reasoning": True,
        "supports_adaptive_thinking": True,
        "supported_openai_params": [],
    }
    assert info["router"]["providers"] == ["openrouter"]
    assert info["embed"]["mode"] == "embedding"


def test_a_virtual_key_discovers_only_its_allowed_routes(client: TestClient) -> None:
    vk = _vk(client, "claude", "gpt")
    assert [m["id"] for m in client.get("/v1/models", headers=vk).json()["data"]] == [
        "claude",
        "gpt",
    ]
    groups = [r["model_group"] for r in client.get("/model_group/info", headers=vk).json()["data"]]
    assert groups == ["claude", "gpt"]


def test_discovery_requires_a_key(client: TestClient) -> None:
    assert client.get("/v1/models").status_code == 401
    assert client.get("/model_group/info").status_code == 401


def test_the_key_header_is_configurable(monkeypatch: pytest.MonkeyPatch) -> None:
    with _client(monkeypatch, MODEL_GATEWAY_API_KEY_HEADER="x-litellm-api-key") as c:
        assert c.get("/v1/models", headers={"x-litellm-api-key": TOKEN}).status_code == 200
        assert (
            c.get("/v1/models", headers={"x-litellm-api-key": "Bearer " + TOKEN}).status_code == 200
        )
        assert c.get("/v1/models", headers={"x-litellm-api-key": "wrong"}).status_code == 401
        assert c.get("/v1/models", headers={"x-api-key": TOKEN}).status_code == 200


# -- Anthropic Messages ----------------------------------------------------


def test_messages_round_trip_signed_thinking_and_tool_use(client: TestClient) -> None:
    tools = [{"name": "lookup", "description": "d", "input_schema": {"type": "object"}}]
    first = client.post(
        "/v1/messages",
        headers=SVC,
        json={
            "model": "claude",
            "max_tokens": 50,
            "thinking": {"type": "adaptive"},
            "tools": tools,
            "messages": [{"role": "user", "content": '/tool lookup {"q": 1}'}],
        },
    )
    assert first.status_code == 200, first.text
    reply = first.json()
    assert [b["type"] for b in reply["content"]] == ["thinking", "tool_use"]
    assert reply["stop_reason"] == "tool_use"
    assert reply["content"][1]["input"] == {"q": 1}
    assert float(first.headers[COST_HEADER]) > 0

    replay = [
        {"role": "user", "content": '/tool lookup {"q": 1}'},
        {"role": "assistant", "content": reply["content"]},
        {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": reply["content"][1]["id"],
                    "content": "found it",
                }
            ],
        },
    ]
    second = client.post(
        "/v1/messages",
        headers=SVC,
        json={
            "model": "claude",
            "max_tokens": 50,
            "thinking": {"type": "adaptive"},
            "tools": tools,
            "messages": replay,
        },
    )
    assert second.status_code == 200, second.text
    assert second.json()["content"][-1] == {"type": "text", "text": "echo:found it"}

    tampered = json.loads(json.dumps(replay))
    tampered[1]["content"][0]["signature"] = "forged"
    rejected = client.post(
        "/v1/messages",
        headers=SVC,
        json={"model": "claude", "max_tokens": 50, "messages": tampered},
    )
    assert rejected.status_code == 400
    assert rejected.json()["detail"]["error"]["code"] == "UPSTREAM_REJECTED"


def test_messages_stream_is_native_sse_and_meters_the_key(client: TestClient) -> None:
    vk = _vk(client, "claude", budget=100)
    r = client.post(
        "/v1/messages",
        headers=vk,
        json={
            "model": "claude",
            "max_tokens": 50,
            "stream": True,
            "messages": [{"role": "user", "content": "hi there"}],
        },
    )
    assert r.status_code == 200
    assert "text/event-stream" in r.headers["content-type"]
    events = _events(r.text)
    assert events[0]["type"] == "message_start"
    assert events[-1]["type"] == "message_stop"
    deltas = "".join(
        e["delta"].get("text", "") for e in events if e["type"] == "content_block_delta"
    )
    assert deltas == "echo:hi there"
    store = client.app.state.virtual_keys
    assert store.spent_usd(vk["Authorization"][7:]) > 0


# -- OpenAI Responses ------------------------------------------------------


def test_responses_round_trip_encrypted_reasoning_and_service_tier(client: TestClient) -> None:
    first = client.post(
        "/v1/responses",
        headers=SVC,
        json={
            "model": "gpt",
            "input": [{"role": "user", "content": [{"type": "input_text", "text": "hi"}]}],
            "reasoning": {"effort": "high", "summary": "auto"},
            "include": ["reasoning.encrypted_content"],
            "service_tier": "ultrafast",
            "store": False,
        },
    )
    assert first.status_code == 200, first.text
    body = first.json()
    assert body["service_tier"] == "ultrafast"
    reasoning, message = body["output"]
    assert reasoning["type"] == "reasoning" and reasoning["encrypted_content"]
    assert message["content"][0]["text"] == "echo:hi"

    replay = [
        {"role": "user", "content": "hi"},
        reasoning,
        message,
        {"role": "user", "content": "again"},
    ]
    ok = client.post("/v1/responses", headers=SVC, json={"model": "gpt", "input": replay})
    assert ok.status_code == 200, ok.text

    stripped = [{**reasoning, "encrypted_content": None}]
    bad = client.post(
        "/v1/responses", headers=SVC, json={"model": "gpt", "input": [*stripped, replay[-1]]}
    )
    assert bad.status_code == 400


def test_responses_stream_ends_with_a_terminal_event(client: TestClient) -> None:
    r = client.post(
        "/v1/responses", headers=SVC, json={"model": "gpt", "input": "yo", "stream": True}
    )
    events = _events(r.text)
    assert events[0]["type"] == "response.created"
    assert events[-1]["type"] == "response.completed"
    assert events[-1]["response"]["usage"]["output_tokens"] > 0


def test_responses_function_call_then_output(client: TestClient) -> None:
    tools = [{"type": "function", "name": "f", "parameters": {"type": "object"}}]
    first = client.post(
        "/v1/responses",
        headers=SVC,
        json={"model": "gpt", "input": '/tool f {"a": "b"}', "tools": tools},
    ).json()
    (call,) = first["output"]
    assert call["type"] == "function_call" and json.loads(call["arguments"]) == {"a": "b"}
    second = client.post(
        "/v1/responses",
        headers=SVC,
        json={
            "model": "gpt",
            "tools": tools,
            "input": [
                call,
                {"type": "function_call_output", "call_id": call["call_id"], "output": "done"},
            ],
        },
    ).json()
    assert second["output"][0]["content"][0]["text"] == "echo:done"


# -- chat, routing, pricing ------------------------------------------------


def test_chat_cost_comes_from_the_route_price(client: TestClient) -> None:
    r = client.post(
        "/v1/chat/completions",
        headers=SVC,
        json={"model": "echo", "messages": [{"role": "user", "content": "a b c"}]},
    )
    usage = r.json()["usage"]
    expected = usage["prompt_tokens"] * 0.001 + usage["completion_tokens"] * 0.002
    assert usage["cost_total"] == pytest.approx(expected)
    assert float(r.headers[COST_HEADER]) == pytest.approx(expected)


def test_chat_stream_tool_call_chunks(client: TestClient) -> None:
    tools = [{"type": "function", "function": {"name": "f", "parameters": {"type": "object"}}}]
    r = client.post(
        "/v1/chat/completions",
        headers=SVC,
        json={
            "model": "echo",
            "stream": True,
            "tools": tools,
            "messages": [{"role": "user", "content": "/tool f {}"}],
        },
    )
    assert r.text.rstrip().endswith("data: [DONE]")
    chunks = _events(r.text)
    assert chunks[0]["choices"][0]["delta"]["tool_calls"][0]["function"]["name"] == "f"
    assert chunks[-1]["choices"][0]["finish_reason"] == "tool_calls"
    assert chunks[-1]["usage"]["completion_tokens"] > 0


def test_a_route_refuses_a_wire_its_provider_does_not_serve(client: TestClient) -> None:
    r = client.post(
        "/v1/messages",
        headers=SVC,
        json={"model": "router", "max_tokens": 5, "messages": [{"role": "user", "content": "x"}]},
    )
    assert r.status_code == 400
    assert r.json()["detail"]["error"]["code"] == "WIRE_UNSUPPORTED"


def test_embedding_routes_are_not_chat(client: TestClient) -> None:
    r = client.post(
        "/v1/chat/completions",
        headers=SVC,
        json={"model": "embed", "messages": [{"role": "user", "content": "x"}]},
    )
    assert r.status_code == 400
    assert r.json()["detail"]["error"]["code"] == "ROUTE_MODE"


def test_budget_exhaustion_is_429_on_every_wire(client: TestClient) -> None:
    vk = _vk(client, "claude", "gpt", budget=0.0)
    for path, body in (
        (
            "/v1/messages",
            {"model": "claude", "max_tokens": 5, "messages": [{"role": "user", "content": "x"}]},
        ),
        ("/v1/responses", {"model": "gpt", "input": "x"}),
    ):
        r = client.post(path, headers=vk, json=body)
        assert r.status_code == 429
        assert r.json()["detail"]["error"]["code"] == "VIRTUAL_KEY_BUDGET_EXCEEDED"


# -- vendor providers ------------------------------------------------------


def test_anthropic_messages_passthrough_preserves_the_body(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[httpx.Request] = []

    def upstream(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            json={
                "id": "msg_1",
                "type": "message",
                "role": "assistant",
                "model": "claude-sonnet-5",
                "content": [{"type": "text", "text": "hey"}],
                "stop_reason": "end_turn",
                "usage": {
                    "input_tokens": 10,
                    "output_tokens": 4,
                    "cache_read_input_tokens": 100,
                    "cache_creation_input_tokens": 0,
                },
            },
        )

    routes = {
        "claude-sonnet-5": {
            "provider": "anthropic",
            "upstream_model": "claude-sonnet-5",
            "input_cost_per_token": 2e-6,
            "output_cost_per_token": 10e-6,
            "cache_read_input_token_cost": 0.2e-6,
        }
    }
    with _client(monkeypatch, routes=routes, ANTHROPIC_API_KEY="sk-ant") as c:
        c.app.state.registry._by_name["anthropic"] = AnthropicProvider(
            api_key="sk-ant", transport=httpx.MockTransport(upstream)
        )
        thinking = {"type": "thinking", "thinking": "t", "signature": "sig-from-anthropic"}
        r = c.post(
            "/v1/messages",
            headers=SVC,
            json={
                "model": "claude-sonnet-5",
                "max_tokens": 64,
                "thinking": {"type": "adaptive"},
                "messages": [
                    {"role": "user", "content": "q"},
                    {"role": "assistant", "content": [thinking]},
                    {"role": "user", "content": "q2"},
                ],
            },
        )
    assert r.status_code == 200, r.text
    (request,) = seen
    assert request.url == "https://api.anthropic.com/v1/messages"
    assert request.headers["x-api-key"] == "sk-ant"
    assert request.headers["anthropic-version"] == "2023-06-01"
    sent = json.loads(request.content)
    assert sent["messages"][1]["content"][0] == thinking
    assert sent["thinking"] == {"type": "adaptive"}
    assert float(r.headers[COST_HEADER]) == pytest.approx(10 * 2e-6 + 4 * 10e-6 + 100 * 0.2e-6)


def test_openai_responses_passthrough_forwards_service_tier(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[dict[str, Any]] = []

    def upstream(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/responses"
        assert request.headers["authorization"] == "Bearer sk-oai"
        seen.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "id": "resp_1",
                "object": "response",
                "status": "completed",
                "output": [],
                "usage": {"input_tokens": 3, "output_tokens": 1},
            },
        )

    routes = {"gpt-6-astra": {"provider": "openai", "upstream_model": "gpt-6-astra"}}
    with _client(monkeypatch, routes=routes, OPENAI_API_KEY="sk-oai") as c:
        c.app.state.registry._by_name["openai"] = OpenAICompatProvider(
            api_key="sk-oai", transport=httpx.MockTransport(upstream)
        )
        r = c.post(
            "/v1/responses",
            headers=SVC,
            json={"model": "gpt-6-astra", "input": "x", "service_tier": "ultrafast"},
        )
    assert r.status_code == 200, r.text
    assert seen == [
        {"model": "gpt-6-astra", "input": "x", "service_tier": "ultrafast", "stream": False}
    ]


def test_an_upstream_client_error_is_relayed_not_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = 0

    def upstream(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(400, json={"error": {"message": "max_tokens too large"}})

    routes = {
        "gpt": {
            "provider": "openai",
            "upstream_model": "gpt",
            "retries": 3,
            "fallback_route_ids": ["echo"],
        },
        "echo": {"provider": "echo", "upstream_model": "echo"},
    }
    with _client(monkeypatch, routes=routes, OPENAI_API_KEY="sk") as c:
        c.app.state.registry._by_name["openai"] = OpenAICompatProvider(
            api_key="sk", transport=httpx.MockTransport(upstream)
        )
        r = c.post(
            "/v1/chat/completions",
            headers=SVC,
            json={"model": "gpt", "messages": [{"role": "user", "content": "x"}]},
        )
    assert r.status_code == 400
    assert "max_tokens too large" in r.json()["detail"]["error"]["message"]
    assert calls == 1


def test_vendor_keys_unlock_their_default_routes(monkeypatch: pytest.MonkeyPatch) -> None:
    routes = {"echo": {"provider": "echo", "upstream_model": "echo"}}
    with _client(monkeypatch, routes=routes, ANTHROPIC_API_KEY="a", OPENAI_API_KEY="o") as c:
        ids = [m["id"] for m in c.get("/v1/models", headers=SVC).json()["data"]]
        assert ids == ["echo", "gpt-5.5", "claude-sonnet-5"]
        assert c.get("/v1/readyz").json() == {"status": "ready"}


def test_readyz_needs_the_anthropic_key_for_anthropic_routes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    routes = {"c": {"provider": "anthropic", "upstream_model": "claude-sonnet-5"}}
    with _client(monkeypatch, routes=routes) as c:
        assert c.get("/v1/readyz").json() == {"status": "not_ready", "reason": "credentials"}


def test_a_forced_tool_choice_calls_that_tool_with_the_users_json(client: TestClient) -> None:
    function = {"name": "browser_result", "parameters": {"type": "object"}}
    chat = client.post(
        "/v1/chat/completions",
        headers=SVC,
        json={
            "model": "echo",
            "tools": [{"type": "function", "function": function}],
            "tool_choice": {"type": "function", "function": {"name": "browser_result"}},
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": '{"done": true}'},
                        {"type": "image_url", "image_url": {"url": "data:image/png;base64,AA=="}},
                    ],
                }
            ],
        },
    ).json()
    assert json.loads(chat["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"]) == {
        "done": True
    }
    messages = client.post(
        "/v1/messages",
        headers=SVC,
        json={
            "model": "claude",
            "max_tokens": 9,
            "tools": [{**function, "input_schema": {"type": "object"}}],
            "tool_choice": {"type": "tool", "name": "browser_result"},
            "messages": [{"role": "user", "content": "not json"}],
        },
    ).json()
    assert messages["content"][-1]["input"] == {}
    responses = client.post(
        "/v1/responses",
        headers=SVC,
        json={
            "model": "gpt",
            "input": '{"x": 1}',
            "tools": [{"type": "function", **function}],
            "tool_choice": {"type": "function", "name": "browser_result"},
        },
    ).json()
    assert json.loads(responses["output"][0]["arguments"]) == {"x": 1}
