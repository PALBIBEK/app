"""The three wire transports: what they send, what they rebuild, how history crosses models."""

from __future__ import annotations

import json
from dataclasses import replace
from typing import Any

import httpx
import pytest

from supervisor.models.catalog import admit
from supervisor.models.client import ModelRouter, RoutedModelClient, completion
from supervisor.models.endpoints import GatewayConfig
from supervisor.models.messages import (
    NO_RESULT,
    AssistantMessage,
    Context,
    TextBlock,
    ThinkingBlock,
    Tool,
    ToolCallBlock,
    ToolResultMessage,
    UserMessage,
    context_from_chat,
    transform_messages,
)
from supervisor.models.registry import resolve_builtin
from supervisor.models.spec import ModelSpec, WireApi
from supervisor.models.transports import ModelCallError, RequestOptions, WireCall
from supervisor.models.transports.anthropic import call_messages
from supervisor.models.transports.completions import call_completions
from supervisor.models.transports.responses import call_responses
from supervisor.seams.core.types import ChatMessage

TOOL = Tool(
    "read",
    "Read a file",
    {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
)


def sse(*events: tuple[str | None, Any]) -> bytes:
    out = b""
    for name, data in events:
        payload = data if isinstance(data, str) else json.dumps(data)
        out += (f"event: {name}\n" if name else "").encode() + f"data: {payload}\n\n".encode()
    return out


class Upstream:
    """Records requests and answers each with the next scripted response."""

    def __init__(self, *responses: httpx.Response) -> None:
        self.responses = list(responses)
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self.responses.pop(0)

    @property
    def body(self) -> dict[str, Any]:
        return json.loads(self.requests[-1].content)

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self))


def event_stream(body: bytes) -> httpx.Response:
    return httpx.Response(200, content=body, headers={"content-type": "text/event-stream"})


def gateway_spec(group: str, **info: Any) -> ModelSpec:
    spec = admit(
        {
            "model_group": group,
            "mode": "chat",
            "supports_function_calling": True,
            "max_input_tokens": 200_000,
            "max_output_tokens": 8_000,
            "input_cost_per_token": 1e-6,
            "output_cost_per_token": 2e-6,
            **info,
        },
        frozenset({group}),
        "https://gw.example/v1",
    )
    assert isinstance(spec, ModelSpec)
    return spec


def call(spec: ModelSpec, upstream: Upstream, context: Context, **options: Any) -> WireCall:
    return WireCall(
        spec=spec,
        context=context,
        api_key="gateway-key",
        http=upstream.client(),
        headers={"x-gateway-key": "gateway-key"},
        options=RequestOptions(max_retries=0, **options),
    )


# -- Anthropic Messages ----------------------------------------------------------

ANTHROPIC_STREAM = sse(
    (
        "message_start",
        {
            "type": "message_start",
            "message": {
                "id": "msg_1",
                "usage": {
                    "input_tokens": 100,
                    "output_tokens": 1,
                    "cache_read_input_tokens": 1000,
                    "cache_creation_input_tokens": 50,
                },
            },
        },
    ),
    (
        "content_block_start",
        {
            "type": "content_block_start",
            "index": 0,
            "content_block": {"type": "thinking", "thinking": ""},
        },
    ),
    (
        "content_block_delta",
        {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "thinking_delta", "thinking": "let me look"},
        },
    ),
    (
        "content_block_delta",
        {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "signature_delta", "signature": "SIG=="},
        },
    ),
    ("content_block_stop", {"type": "content_block_stop", "index": 0}),
    (
        "content_block_start",
        {
            "type": "content_block_start",
            "index": 1,
            "content_block": {"type": "tool_use", "id": "toolu_1", "name": "read", "input": {}},
        },
    ),
    (
        "content_block_delta",
        {
            "type": "content_block_delta",
            "index": 1,
            "delta": {"type": "input_json_delta", "partial_json": '{"path": "a.'},
        },
    ),
    (
        "content_block_delta",
        {
            "type": "content_block_delta",
            "index": 1,
            "delta": {"type": "input_json_delta", "partial_json": 'txt"}'},
        },
    ),
    ("content_block_stop", {"type": "content_block_stop", "index": 1}),
    (
        "message_delta",
        {
            "type": "message_delta",
            "delta": {"stop_reason": "tool_use"},
            "usage": {"output_tokens": 40},
        },
    ),
    ("message_stop", {"type": "message_stop"}),
)


def test_anthropic_request_carries_caching_thinking_and_affinity() -> None:
    spec = gateway_spec(
        "claude", providers=["anthropic"], supports_reasoning=True, supports_adaptive_thinking=True
    ).evolve(base_url="https://gw.example")
    upstream = Upstream(event_stream(ANTHROPIC_STREAM))
    context = Context("be brief", (UserMessage("hi"),), (TOOL,))
    turn = call_messages(call(spec, upstream, context, session_id="s-1", thinking="high"))

    request = upstream.requests[0]
    assert str(request.url) == "https://gw.example/v1/messages"
    assert request.headers["x-api-key"] == "gateway-key"
    assert request.headers["x-gateway-key"] == "gateway-key"
    assert request.headers["anthropic-version"] == "2023-06-01"
    assert request.headers["x-session-affinity"] == "s-1"
    assert "anthropic-beta" not in request.headers  # adaptive thinking interleaves natively
    body = upstream.body
    assert body["stream"] is True
    assert body["system"] == [
        {"type": "text", "text": "be brief", "cache_control": {"type": "ephemeral"}}
    ]
    assert body["messages"] == [
        {
            "role": "user",
            "content": [{"type": "text", "text": "hi", "cache_control": {"type": "ephemeral"}}],
        }
    ]
    assert body["tools"][0]["eager_input_streaming"] is True
    assert body["tools"][0]["cache_control"] == {"type": "ephemeral"}
    assert body["tools"][0]["input_schema"]["required"] == ["path"]
    assert body["thinking"] == {"type": "adaptive", "display": "summarized"}
    assert body["output_config"] == {"effort": "high"}
    assert body["max_tokens"] == 8_000

    assert turn.blocks == (
        ThinkingBlock("let me look", "SIG=="),
        ToolCallBlock("toolu_1", "read", {"path": "a.txt"}),
    )
    assert turn.stop_reason == "toolUse"
    assert (turn.usage.input, turn.usage.output, turn.usage.cache_read, turn.usage.cache_write) == (
        100,
        40,
        1000,
        50,
    )
    assert turn.cost.total == pytest.approx((100 + 1000 + 50) * 1e-6 + 40 * 2e-6)


def test_anthropic_budget_thinking_for_non_adaptive_models() -> None:
    spec = resolve_builtin("claude-haiku-4-5")
    assert spec is not None
    upstream = Upstream(event_stream(ANTHROPIC_STREAM))
    call_messages(call(spec, upstream, Context(messages=(UserMessage("hi"),)), thinking="medium"))
    body = upstream.body
    assert body["thinking"] == {"type": "enabled", "budget_tokens": 8192, "display": "summarized"}
    assert body["max_tokens"] == 64_000
    assert upstream.requests[0].headers["anthropic-beta"] == "interleaved-thinking-2025-05-14"

    upstream = Upstream(event_stream(ANTHROPIC_STREAM))
    call_messages(call(spec, upstream, Context(messages=(UserMessage("hi"),)), thinking="off"))
    assert upstream.body["thinking"] == {"type": "disabled"}


def test_anthropic_replays_signed_thinking_and_groups_tool_results() -> None:
    spec = resolve_builtin("claude-opus-5")
    assert spec is not None
    history = (
        UserMessage("go"),
        AssistantMessage(
            (
                ThinkingBlock("plan", "SIG"),
                ToolCallBlock("toolu_1", "read", {"path": "a"}),
                ToolCallBlock("toolu_2", "read", {"path": "b"}),
            ),
            model=spec.id,
            api=WireApi.ANTHROPIC_MESSAGES,
        ),
        ToolResultMessage("toolu_1", "read", "A"),
        ToolResultMessage("toolu_2", "read", '{"ok": false}', is_error=True),
    )
    upstream = Upstream(event_stream(ANTHROPIC_STREAM))
    call_messages(call(spec, upstream, Context(messages=history), cache_retention="none"))
    messages = upstream.body["messages"]
    assert messages[1]["content"][0] == {"type": "thinking", "thinking": "plan", "signature": "SIG"}
    assert messages[2] == {
        "role": "user",
        "content": [
            {"type": "tool_result", "tool_use_id": "toolu_1", "content": "A", "is_error": False},
            {
                "type": "tool_result",
                "tool_use_id": "toolu_2",
                "content": '{"ok": false}',
                "is_error": True,
            },
        ],
    }


def test_anthropic_refusals_and_truncated_streams_fail_the_call() -> None:
    spec = resolve_builtin("claude-opus-5")
    assert spec is not None
    refusal = sse(
        ("message_start", {"type": "message_start", "message": {"id": "m", "usage": {}}}),
        ("message_delta", {"type": "message_delta", "delta": {"stop_reason": "refusal"}}),
        ("message_stop", {"type": "message_stop"}),
    )
    with pytest.raises(ModelCallError, match="refused"):
        call_messages(
            call(spec, Upstream(event_stream(refusal)), Context(messages=(UserMessage("x"),)))
        )
    truncated = sse(
        ("message_start", {"type": "message_start", "message": {"id": "m", "usage": {}}})
    )
    with pytest.raises(ModelCallError, match="before message_stop"):
        call_messages(
            call(spec, Upstream(event_stream(truncated)), Context(messages=(UserMessage("x"),)))
        )


# -- OpenAI Responses --------------------------------------------------------------

REASONING_ITEM = {
    "type": "reasoning",
    "id": "rs_1",
    "summary": [{"type": "summary_text", "text": "thought"}],
    "encrypted_content": "ENC",
}
RESPONSES_STREAM = sse(
    (None, {"type": "response.created", "response": {"id": "resp_1"}}),
    (
        None,
        {
            "type": "response.output_item.added",
            "output_index": 0,
            "item": {"type": "reasoning", "id": "rs_1", "summary": []},
        },
    ),
    (
        None,
        {"type": "response.reasoning_summary_text.delta", "output_index": 0, "delta": "thought"},
    ),
    (None, {"type": "response.output_item.done", "output_index": 0, "item": REASONING_ITEM}),
    (
        None,
        {
            "type": "response.output_item.added",
            "output_index": 1,
            "item": {"type": "message", "id": "msg_1", "content": []},
        },
    ),
    (None, {"type": "response.output_text.delta", "output_index": 1, "delta": "hel"}),
    (None, {"type": "response.output_text.delta", "output_index": 1, "delta": "lo"}),
    (
        None,
        {
            "type": "response.output_item.done",
            "output_index": 1,
            "item": {
                "type": "message",
                "id": "msg_1",
                "content": [{"type": "output_text", "text": "hello"}],
            },
        },
    ),
    (
        None,
        {
            "type": "response.output_item.added",
            "output_index": 2,
            "item": {
                "type": "function_call",
                "id": "fc_1",
                "call_id": "call_1",
                "name": "read",
                "arguments": "",
            },
        },
    ),
    (
        None,
        {
            "type": "response.function_call_arguments.delta",
            "output_index": 2,
            "delta": '{"path":"x"}',
        },
    ),
    (
        None,
        {
            "type": "response.output_item.done",
            "output_index": 2,
            "item": {
                "type": "function_call",
                "id": "fc_1",
                "call_id": "call_1",
                "name": "read",
                "arguments": '{"path":"x"}',
            },
        },
    ),
    (
        None,
        {
            "type": "response.completed",
            "response": {
                "id": "resp_1",
                "status": "completed",
                "service_tier": "ultrafast",
                "output": [REASONING_ITEM],
                "usage": {
                    "input_tokens": 1200,
                    "input_tokens_details": {"cached_tokens": 1000},
                    "output_tokens": 30,
                    "output_tokens_details": {"reasoning_tokens": 10},
                },
            },
        },
    ),
)


def test_responses_request_is_stateless_with_encrypted_reasoning() -> None:
    spec = gateway_spec(
        "gpt",
        providers=["openai"],
        supports_reasoning=True,
        supported_openai_params=["reasoning_effort"],
    )
    upstream = Upstream(event_stream(RESPONSES_STREAM))
    turn = call_responses(
        call(
            spec,
            upstream,
            Context("sys", (UserMessage("hi"),), (TOOL,)),
            session_id="s-1",
            thinking="high",
        )
    )
    request = upstream.requests[0]
    assert str(request.url) == "https://gw.example/v1/responses"
    assert request.headers["authorization"] == "Bearer gateway-key"
    assert request.headers["session_id"] == "s-1"
    assert request.headers["x-client-request-id"] == "s-1"
    body = upstream.body
    assert body["input"][0] == {
        "role": "system",
        "content": "sys",
    }  # gateway groups never use developer
    assert body["input"][1] == {"role": "user", "content": [{"type": "input_text", "text": "hi"}]}
    assert body["store"] is False and body["stream"] is True
    assert body["prompt_cache_key"] == "s-1"
    assert body["reasoning"] == {"effort": "high", "summary": "auto"}
    assert body["include"] == ["reasoning.encrypted_content"]
    assert body["max_output_tokens"] == 8_000
    assert body["tools"] == [
        {
            "type": "function",
            "name": "read",
            "description": "Read a file",
            "parameters": dict(TOOL.parameters),
        }
    ]

    thinking, text, tool = turn.blocks
    assert (
        isinstance(thinking, ThinkingBlock)
        and json.loads(thinking.signature or "") == REASONING_ITEM
    )
    assert isinstance(text, TextBlock) and text.text == "hello"
    assert json.loads(text.signature or "") == {"v": 1, "id": "msg_1"}
    assert tool == ToolCallBlock("call_1|fc_1", "read", {"path": "x"})
    assert turn.stop_reason == "toolUse"
    assert (turn.usage.input, turn.usage.cache_read, turn.usage.reasoning) == (200, 1000, 10)
    assert turn.service_tier == "ultrafast"


def test_responses_replays_reasoning_items_message_ids_and_call_ids() -> None:
    spec = gateway_spec("gpt", providers=["openai"], supports_reasoning=True)
    history = (
        UserMessage("hi"),
        AssistantMessage(
            (
                ThinkingBlock("thought", json.dumps(REASONING_ITEM)),
                TextBlock("hello", '{"v": 1, "id": "msg_1"}'),
                ToolCallBlock("call_1|fc_1", "read", {"path": "x"}),
            ),
            model=spec.id,
            api=WireApi.OPENAI_RESPONSES,
        ),
        ToolResultMessage("call_1|fc_1", "read", ""),
    )
    upstream = Upstream(event_stream(RESPONSES_STREAM))
    call_responses(call(spec, upstream, Context(messages=history)))
    items = upstream.body["input"]
    assert items[1] == REASONING_ITEM
    assert items[2]["id"] == "msg_1" and items[2]["content"][0]["text"] == "hello"
    assert items[3] == {
        "type": "function_call",
        "id": "fc_1",
        "call_id": "call_1",
        "name": "read",
        "arguments": '{"path": "x"}',
    }
    assert items[4] == {
        "type": "function_call_output",
        "call_id": "call_1",
        "output": "(no tool output)",
    }
    assert upstream.body["reasoning"] == {
        "effort": "none"
    }  # thinking off, and the model allows "none"


def test_responses_without_a_terminal_event_fails() -> None:
    spec = gateway_spec("gpt", providers=["openai"])
    partial = sse((None, {"type": "response.created", "response": {"id": "r"}}))
    with pytest.raises(ModelCallError, match="terminal"):
        call_responses(
            call(spec, Upstream(event_stream(partial)), Context(messages=(UserMessage("x"),)))
        )


def test_priority_and_flex_tiers_reprice_the_call() -> None:
    spec = resolve_builtin("gpt-5.5")
    assert spec is not None
    stream = RESPONSES_STREAM.replace(b'"service_tier": "ultrafast"', b'"service_tier": "priority"')
    turn = call_responses(
        call(spec, Upstream(event_stream(stream)), Context(messages=(UserMessage("x"),)))
    )
    assert turn.cost.total == pytest.approx(spec.pricing.cost(turn.usage).total * 2.5)


# -- chat completions -----------------------------------------------------------

CHAT_STREAM = sse(
    (
        None,
        {"id": "c1", "choices": [{"index": 0, "delta": {"role": "assistant", "content": "ok"}}]},
    ),
    (
        None,
        {
            "id": "c1",
            "choices": [
                {
                    "index": 0,
                    "delta": {
                        "tool_calls": [
                            {
                                "index": 0,
                                "id": "call_9",
                                "function": {"name": "read", "arguments": '{"pa'},
                            }
                        ]
                    },
                }
            ],
        },
    ),
    (
        None,
        {
            "id": "c1",
            "choices": [
                {
                    "index": 0,
                    "delta": {"tool_calls": [{"index": 0, "function": {"arguments": 'th": "z"}'}}]},
                }
            ],
        },
    ),
    (None, {"id": "c1", "choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]}),
    (
        None,
        {
            "id": "c1",
            "choices": [],
            "usage": {
                "prompt_tokens": 50,
                "completion_tokens": 5,
                "prompt_tokens_details": {"cached_tokens": 20},
            },
        },
    ),
    (None, "[DONE]"),
)


def test_chat_completions_request_and_tool_call_reassembly() -> None:
    spec = gateway_spec("mixed", providers=["vertex_ai", "bedrock"])
    upstream = Upstream(event_stream(CHAT_STREAM))
    history = (
        UserMessage("hi"),
        AssistantMessage((ToolCallBlock("call_1|fc_long", "read", {}),), model="gateway/other"),
        ToolResultMessage("call_1|fc_long", "read", "done"),
    )
    turn = call_completions(
        call(spec, upstream, Context("sys", history, (TOOL,)), session_id="s-1")
    )
    request = upstream.requests[0]
    assert str(request.url) == "https://gw.example/v1/chat/completions"
    for header in ("session_id", "x-client-request-id", "x-session-affinity"):
        assert request.headers[header] == "s-1"
    body = upstream.body
    assert body["messages"][0] == {"role": "system", "content": "sys"}
    assert body["messages"][2]["tool_calls"][0]["id"] == "call_1_fc_long"
    assert body["messages"][3] == {
        "role": "tool",
        "content": "done",
        "tool_call_id": "call_1_fc_long",
    }
    assert body["stream_options"] == {"include_usage": True}
    assert body["max_tokens"] == 8_000  # the group does not advertise max_completion_tokens
    assert "store" not in body and "reasoning_effort" not in body
    assert "strict" not in body["tools"][0]["function"]
    assert turn.blocks == (TextBlock("ok"), ToolCallBlock("call_9", "read", {"path": "z"}))
    assert turn.stop_reason == "toolUse"
    assert (turn.usage.input, turn.usage.cache_read) == (30, 20)


def test_chat_completions_without_finish_reason_fails() -> None:
    spec = gateway_spec("mixed")
    stream = sse(
        (None, {"id": "c", "choices": [{"index": 0, "delta": {"content": "x"}}]}), (None, "[DONE]")
    )
    with pytest.raises(ModelCallError, match="finish_reason"):
        call_completions(
            call(spec, Upstream(event_stream(stream)), Context(messages=(UserMessage("x"),)))
        )


# -- shared behaviour ------------------------------------------------------------


def test_transient_failures_retry_and_rejections_do_not() -> None:
    spec = gateway_spec("mixed")
    upstream = Upstream(
        httpx.Response(
            529, headers={"retry-after-ms": "1"}, json={"error": {"message": "overloaded"}}
        ),
        event_stream(CHAT_STREAM),
    )
    turn = call_completions(
        _with_retries(call(spec, upstream, Context(messages=(UserMessage("x"),))))
    )
    assert len(upstream.requests) == 2 and turn.stop_reason == "toolUse"

    rejecting = Upstream(
        httpx.Response(
            429,
            json={
                "detail": {
                    "error": {
                        "code": "VIRTUAL_KEY_BUDGET_EXCEEDED",
                        "message": "virtual key budget exceeded",
                    }
                }
            },
        )
    )
    with pytest.raises(ModelCallError) as caught:
        call_completions(
            _with_retries(call(spec, rejecting, Context(messages=(UserMessage("x"),))))
        )
    assert caught.value.status == 429 and caught.value.code == "VIRTUAL_KEY_BUDGET_EXCEEDED"
    assert caught.value.retryable is False

    bad = Upstream(
        httpx.Response(400, json={"error": {"message": "max_tokens too large", "type": "invalid"}})
    )
    with pytest.raises(ModelCallError, match="max_tokens too large") as caught:
        call_completions(_with_retries(call(spec, bad, Context(messages=(UserMessage("x"),)))))
    assert len(bad.requests) == 1 and caught.value.retryable is False


def _with_retries(wire: WireCall) -> WireCall:
    return replace(wire, options=replace(wire.options, max_retries=2))


# -- history across models -------------------------------------------------------


def test_native_reasoning_only_returns_to_the_model_that_produced_it() -> None:
    opus = resolve_builtin("claude-opus-5")
    sonnet = resolve_builtin("claude-sonnet-5")
    assert opus is not None and sonnet is not None
    turn = AssistantMessage(
        (
            ThinkingBlock("plan", "SIG"),
            ThinkingBlock("[Reasoning redacted]", "DATA", redacted=True),
            TextBlock("answer"),
            ToolCallBlock("call|weird id", "read", {}),
        ),
        model=opus.id,
        api=WireApi.ANTHROPIC_MESSAGES,
    )
    same = transform_messages([turn], opus)[0]
    assert isinstance(same, AssistantMessage) and same.blocks[:2] == turn.blocks[:2]

    from supervisor.models.transports.anthropic import normalize_tool_call_id

    crossed = transform_messages(
        [turn, ToolResultMessage("call|weird id", "read", "r")], sonnet, normalize_tool_call_id
    )
    assistant, result = crossed
    assert isinstance(assistant, AssistantMessage)
    assert assistant.blocks == (
        TextBlock("plan"),
        TextBlock("answer"),
        ToolCallBlock("call_weird_id", "read", {}),
    )
    assert isinstance(result, ToolResultMessage) and result.tool_call_id == "call_weird_id"


def test_orphaned_tool_calls_get_synthetic_results_and_failed_turns_are_dropped() -> None:
    spec = resolve_builtin("claude-opus-5")
    assert spec is not None
    messages = transform_messages(
        [
            AssistantMessage((ToolCallBlock("a", "read", {}),), model=spec.id),
            UserMessage("never mind"),
            AssistantMessage((TextBlock("partial"),), model=spec.id, stop_reason="error"),
        ],
        spec,
    )
    assert messages[1] == ToolResultMessage("a", "read", NO_RESULT, is_error=True)
    assert len(messages) == 3


def test_chat_history_becomes_a_context_with_native_parts() -> None:
    parts = [
        {
            "type": "reasoning",
            "text": "t",
            "signature": "S",
            "redacted": False,
            "api": "anthropic-messages",
            "model": "claude-opus-5",
        },
        {"type": "text", "text": "a"},
        {"type": "tool_call", "id": "c1", "name": "read", "arguments": {"path": "p"}},
    ]
    context = context_from_chat(
        [
            ChatMessage("system", "sys"),
            ChatMessage("user", "hi"),
            ChatMessage(
                "assistant",
                "a",
                tool_calls=[{"id": "c1", "name": "read", "arguments": {}}],
                parts=parts,
                model="claude-opus-5",
            ),
            ChatMessage("tool", '{"ok": false, "error": "no"}', tool_call_id="c1"),
            ChatMessage("system", "a memory note"),
        ],
        [
            {
                "type": "function",
                "function": {"name": "read", "description": "d", "parameters": {"type": "object"}},
            }
        ],
    )
    assert context.system_prompt == "sys"
    _, assistant, result, note = context.messages
    assert isinstance(assistant, AssistantMessage) and assistant.api is WireApi.ANTHROPIC_MESSAGES
    assert assistant.blocks == (
        ThinkingBlock("t", "S"),
        TextBlock("a"),
        ToolCallBlock("c1", "read", {"path": "p"}),
    )
    assert result == ToolResultMessage("c1", "read", '{"ok": false, "error": "no"}', is_error=True)
    assert note == UserMessage("a memory note")
    assert context.tools == (Tool("read", "d", {"type": "object"}),)


# -- the routed client ------------------------------------------------------------


@pytest.mark.parametrize("base_id", ["gpt-6-astra", "gpt-6.1-sol"])
def test_ultrafast_reaches_the_wire_through_the_gateway(base_id: str) -> None:
    model_id = f"{base_id}-ultrafast"
    seen: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(404)
        seen.append(request)
        return event_stream(RESPONSES_STREAM)

    router = ModelRouter(
        GatewayConfig(
            "https://gateway.example.test/v1",
            "gateway-key",
            "api-key",
            {model_id: f"router/{base_id}"},
        ),
        transport=httpx.MockTransport(handle),
    )
    client = RoutedModelClient(router, session_id="s-1", api_key="vk_session")
    done = client.complete([ChatMessage("user", "hello")], [], model=model_id)
    body = json.loads(seen[0].content)
    assert str(seen[0].url) == "https://gateway.example.test/v1/responses"
    assert body["model"] == f"router/{base_id}"
    assert body["service_tier"] == "ultrafast"
    assert seen[0].headers["api-key"] == "vk_session"
    assert done.model == model_id
    assert done.finish_reason == "tool_calls"
    assert done.message.parts is not None and done.message.parts[0]["type"] == "reasoning"
    assert done.usage is not None and done.usage["cost_total"] > 0
    assert router.audit.records()[-1].model == model_id


def test_completion_view_keeps_text_calls_and_parts() -> None:
    spec = resolve_builtin("claude-opus-5")
    assert spec is not None
    from supervisor.models.spec import Cost, TokenUsage
    from supervisor.models.transports import AssistantTurn

    turn = AssistantTurn(
        (ThinkingBlock("t", "S"), TextBlock("hi"), ToolCallBlock("c", "read", {"path": "x"})),
        TokenUsage(input=10, output=5, cache_read=100),
        Cost(0.1, 0.2),
        "toolUse",
    )
    done = completion(spec, turn)
    assert done.message.content == "hi"
    assert done.message.tool_calls == [{"id": "c", "name": "read", "arguments": {"path": "x"}}]
    assert [p["type"] for p in done.message.parts or []] == ["reasoning", "text", "tool_call"]
    assert done.usage == {
        "prompt_tokens": 110,
        "completion_tokens": 5,
        "total_tokens": 115,
        "input_tokens": 10,
        "output_tokens": 5,
        "cache_read": 100,
        "cache_write": 0,
        "reasoning_tokens": 0,
        "cost": pytest.approx(0.3),
        "cost_total": pytest.approx(0.3),
    }


def test_every_transport_hands_the_reply_over_as_it_streams() -> None:
    """``on_delta`` sees text and thinking in the order they arrive (streamed partials),
    and a hook that fails never fails the call."""
    cases = [
        (call_completions, CHAT_STREAM, gateway_spec("mixed")),
        (
            call_messages,
            ANTHROPIC_STREAM,
            gateway_spec("claude", providers=["anthropic"]).evolve(base_url="https://gw.example"),
        ),
        (call_responses, RESPONSES_STREAM, gateway_spec("gpt", providers=["openai"])),
    ]
    for transport, stream, spec in cases:
        seen: list[tuple[str, str]] = []
        transport(call(spec, Upstream(event_stream(stream)), Context("s", (UserMessage("hi"),), (TOOL,)),
                       on_delta=lambda kind, chunk, seen=seen: seen.append((kind, chunk))))
        assert seen, transport.__name__
        assert {kind for kind, _ in seen} <= {"text", "thinking"}

        def broken(kind: str, chunk: str) -> None:
            raise RuntimeError("display failed")

        transport(call(spec, Upstream(event_stream(stream)), Context("s", (UserMessage("hi"),), (TOOL,)),
                       on_delta=broken))
    seen = []
    call_messages(call(cases[1][2], Upstream(event_stream(ANTHROPIC_STREAM)), Context("s", (UserMessage("hi"),), (TOOL,)),
                       on_delta=lambda kind, chunk: seen.append((kind, chunk))))
    assert seen == [("thinking", "let me look")]
