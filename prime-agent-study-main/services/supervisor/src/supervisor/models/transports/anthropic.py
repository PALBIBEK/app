"""Anthropic Messages, as pi-ai speaks it.

Signed thinking blocks are kept verbatim and replayed to the model that signed
them, which is what lets extended thinking continue across tool calls. Prompts
are cached: the system prompt, the last tool and the last user turn carry
``cache_control``.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from supervisor.models.messages import (
    AssistantBlock,
    AssistantMessage,
    Context,
    TextBlock,
    ThinkingBlock,
    Tool,
    ToolCallBlock,
    ToolResultMessage,
    UserMessage,
    clamp_max_tokens,
    transform_messages,
)
from supervisor.models.spec import ModelSpec, ThinkingLevel, TokenUsage
from supervisor.models.transports.base import (
    AssistantTurn,
    Body,
    CacheRetention,
    ModelCallError,
    StopReason,
    WireCall,
    merged_headers,
    parse_json,
    parse_partial_json,
    stream_events,
)

ANTHROPIC_VERSION = "2023-06-01"
FINE_GRAINED_TOOL_STREAMING_BETA = "fine-grained-tool-streaming-2025-05-14"
INTERLEAVED_THINKING_BETA = "interleaved-thinking-2025-05-14"
_MESSAGE_EVENTS = frozenset(
    {
        "message_start",
        "message_delta",
        "message_stop",
        "content_block_start",
        "content_block_delta",
        "content_block_stop",
    }
)
_THINKING_BUDGETS: Mapping[str, int] = {"minimal": 1024, "low": 2048, "medium": 8192, "high": 16384}
_MIN_OUTPUT_TOKENS = 1024
_INVALID_ID_CHARS = re.compile(r"[^a-zA-Z0-9_-]")


def normalize_tool_call_id(call_id: str, _source: AssistantMessage | None = None) -> str:
    """Anthropic ids match ``^[a-zA-Z0-9_-]+$`` and are at most 64 characters."""
    return _INVALID_ID_CHARS.sub("_", call_id)[:64]


def cache_control(spec: ModelSpec, retention: CacheRetention) -> Body | None:
    if retention == "none":
        return None
    long = retention == "long" and spec.compat.supports_long_cache_retention is not False
    return {"type": "ephemeral", **({"ttl": "1h"} if long else {})}


def thinking_effort(spec: ModelSpec, level: ThinkingLevel) -> str:
    mapped = spec.thinking_level_map.get(level)
    if isinstance(mapped, str):
        return mapped
    return {"minimal": "low", "low": "low", "medium": "medium", "high": "high"}.get(level, "high")


def thinking_budget(max_tokens: int, model_max: int, level: ThinkingLevel) -> tuple[int, int]:
    """(output cap, thinking budget) for budget-based thinking: the budget sits inside the cap."""
    clamped = "high" if level in ("xhigh", "max") else level
    budget = _THINKING_BUDGETS.get(clamped, _THINKING_BUDGETS["high"])
    cap = min(max_tokens + budget, model_max)
    if cap <= budget:
        budget = max(0, cap - _MIN_OUTPUT_TOKENS)
    return cap, budget


@dataclass(slots=True)
class _Plan:
    max_tokens: int
    thinking: Body | None = None
    output_config: Body | None = None
    thinking_enabled: bool = False


def _plan(call: WireCall) -> _Plan:
    spec, options = call.spec, call.options
    base = clamp_max_tokens(spec, call.context, options.max_tokens or spec.max_tokens)
    level = options.thinking
    if not spec.reasoning:
        return _Plan(base)
    if level == "off":
        disabled = spec.thinking_level_map.get("off", "") is not None
        return _Plan(base, thinking={"type": "disabled"} if disabled else None)
    if spec.compat.force_adaptive_thinking:
        return _Plan(
            base,
            thinking={"type": "adaptive", "display": "summarized"},
            output_config={"effort": thinking_effort(spec, level)},
            thinking_enabled=True,
        )
    cap, budget = thinking_budget(base, spec.max_tokens, level)
    cap = clamp_max_tokens(spec, call.context, cap)
    return _Plan(
        cap,
        thinking={
            "type": "enabled",
            "budget_tokens": min(budget, max(0, cap - _MIN_OUTPUT_TOKENS)),
            "display": "summarized",
        },
        thinking_enabled=True,
    )


def build_request(call: WireCall) -> tuple[str, dict[str, str], Body]:
    spec, options, context = call.spec, call.options, call.context
    control = cache_control(spec, options.cache_retention)
    plan = _plan(call)
    betas: list[str] = []
    eager = spec.compat.supports_eager_tool_input_streaming is not False
    if context.tools and not eager:
        betas.append(FINE_GRAINED_TOOL_STREAMING_BETA)
    if not spec.compat.force_adaptive_thinking:
        betas.append(INTERLEAVED_THINKING_BETA)
    affinity = (
        {"x-session-affinity": options.session_id}
        if options.session_id
        and options.cache_retention != "none"
        and spec.compat.send_session_affinity_headers
        else {}
    )
    headers = merged_headers(
        {
            "accept": "application/json",
            "anthropic-version": ANTHROPIC_VERSION,
            "x-api-key": call.api_key,
            **({"anthropic-beta": ",".join(betas)} if betas else {}),
        },
        affinity,
        spec.headers,
        call.headers,
    )
    body: Body = {
        "model": spec.id,
        "messages": convert_messages(context, spec, control),
        "max_tokens": plan.max_tokens,
        "stream": True,
    }
    if context.system_prompt:
        body["system"] = [
            {
                "type": "text",
                "text": context.system_prompt,
                **({"cache_control": control} if control else {}),
            }
        ]
    if (
        options.temperature is not None
        and not plan.thinking_enabled
        and spec.compat.supports_temperature is not False
    ):
        body["temperature"] = options.temperature
    if context.tools:
        tool_control = control if spec.compat.supports_cache_control_on_tools is not False else None
        body["tools"] = convert_tools(context.tools, eager=eager, cache=tool_control)
    if plan.thinking is not None:
        body["thinking"] = plan.thinking
    if plan.output_config is not None:
        body["output_config"] = plan.output_config
    if options.tool_choice is not None:
        choice = options.tool_choice
        body["tool_choice"] = {"type": choice} if isinstance(choice, str) else choice
    return f"{spec.base_url}/v1/messages", headers, body


def convert_tools(tools: tuple[Tool, ...], *, eager: bool, cache: Body | None) -> list[Body]:
    converted: list[Body] = []
    for index, tool in enumerate(tools):
        schema = tool.parameters
        converted.append(
            {
                "name": tool.name,
                "description": tool.description,
                **({"eager_input_streaming": True} if eager else {}),
                "input_schema": {
                    "type": "object",
                    "properties": schema.get("properties") or {},
                    "required": schema.get("required") or [],
                },
                **({"cache_control": cache} if cache and index == len(tools) - 1 else {}),
            }
        )
    return converted


def convert_messages(context: Context, spec: ModelSpec, control: Body | None) -> list[Body]:
    messages = transform_messages(context.messages, spec, normalize_tool_call_id)
    params: list[Body] = []
    i = 0
    while i < len(messages):
        message = messages[i]
        if isinstance(message, UserMessage):
            if message.images:
                blocks: list[Body] = (
                    [{"type": "text", "text": message.text}] if message.text.strip() else []
                )
                blocks += [
                    {
                        "type": "image",
                        "source": {"type": "base64", "media_type": i.mime_type, "data": i.data},
                    }
                    for i in message.images
                ]
                params.append({"role": "user", "content": blocks})
            elif message.text.strip():
                params.append({"role": "user", "content": message.text})
        elif isinstance(message, AssistantMessage):
            blocks = _assistant_blocks(message.blocks, spec)
            if blocks:
                params.append({"role": "assistant", "content": blocks})
        else:
            results: list[Body] = []
            while i < len(messages) and isinstance(messages[i], ToolResultMessage):
                result = messages[i]
                assert isinstance(result, ToolResultMessage)
                results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": result.tool_call_id,
                        "content": result.text,
                        "is_error": result.is_error,
                    }
                )
                i += 1
            params.append({"role": "user", "content": results})
            continue
        i += 1
    if control and params and params[-1]["role"] == "user":
        last = params[-1]
        if isinstance(last["content"], str):
            last["content"] = [{"type": "text", "text": last["content"], "cache_control": control}]
        elif last["content"] and last["content"][-1].get("type") in (
            "text",
            "image",
            "tool_result",
        ):
            last["content"][-1]["cache_control"] = control
    return params


def _assistant_blocks(blocks: tuple[AssistantBlock, ...], spec: ModelSpec) -> list[Body]:
    out: list[Body] = []
    for block in blocks:
        match block:
            case TextBlock(text, _):
                if text.strip():
                    out.append({"type": "text", "text": text})
            case ThinkingBlock(thinking, signature, redacted):
                if redacted:
                    out.append({"type": "redacted_thinking", "data": signature})
                    continue
                signed = bool(signature and signature.strip())
                if not thinking.strip() and not signed:
                    continue
                if signed:
                    out.append({"type": "thinking", "thinking": thinking, "signature": signature})
                elif spec.compat.allow_empty_signature:
                    out.append({"type": "thinking", "thinking": thinking, "signature": ""})
                else:
                    out.append({"type": "text", "text": thinking})
            case ToolCallBlock(call_id, name, arguments):
                out.append(
                    {"type": "tool_use", "id": call_id, "name": name, "input": dict(arguments)}
                )
    return out


@dataclass(slots=True)
class _Open:
    kind: str
    index: int
    text: str = ""
    signature: str = ""
    redacted: bool = False
    call_id: str = ""
    name: str = ""
    partial_json: str = ""
    initial_input: dict[str, Any] = field(default_factory=dict)

    def block(self) -> AssistantBlock:
        if self.kind == "text":
            return TextBlock(self.text)
        if self.kind == "thinking":
            return ThinkingBlock(self.text, self.signature or None, self.redacted)
        arguments = (
            parse_partial_json(self.partial_json) if self.partial_json else self.initial_input
        )
        return ToolCallBlock(self.call_id, self.name, arguments)


_STOP: Mapping[str, StopReason] = {
    "end_turn": "stop",
    "max_tokens": "length",
    "tool_use": "toolUse",
    "pause_turn": "stop",
    "stop_sequence": "stop",
}


def call_messages(call: WireCall) -> AssistantTurn:
    url, headers, body = build_request(call)
    stream = _MessageStream()
    for event in stream_events(call, url, headers, body):
        if event.event == "error":
            raise ModelCallError(event.data, error_type="stream_error")
        if event.event in _MESSAGE_EVENTS:
            stream.feed(parse_json(event.data, f"Anthropic {event.event}"))
    if stream.started and not stream.finished:
        raise ModelCallError("Anthropic stream ended before message_stop")
    usage = stream.usage
    tokens = TokenUsage(
        input=usage.get("input_tokens", 0),
        output=usage.get("output_tokens", 0),
        cache_read=usage.get("cache_read_input_tokens", 0),
        cache_write=usage.get("cache_creation_input_tokens", 0),
        cache_write_1h=usage.get("ephemeral_1h_input_tokens", 0),
        reasoning=usage.get("thinking_tokens", 0),
    )
    return AssistantTurn(
        blocks=tuple(b.block() for b in stream.blocks),
        usage=tokens,
        cost=call.spec.pricing.cost(tokens),
        stop_reason=stream.stop,
        response_id=stream.response_id,
    )


class _MessageStream:
    """Folds an Anthropic Messages event stream into content blocks."""

    def __init__(self) -> None:
        self.blocks: list[_Open] = []
        self.usage: dict[str, int] = {}
        self.stop: StopReason = "stop"
        self.response_id: str | None = None
        self.started = self.finished = False

    def feed(self, payload: Mapping[str, Any]) -> None:
        match payload.get("type"):
            case "message_start":
                self.started = True
                message = payload.get("message") or {}
                self.response_id = message.get("id")
                _merge_usage(self.usage, message.get("usage") or {})
            case "message_stop":
                self.finished = True
            case "content_block_start":
                opened = _open_block(
                    payload.get("content_block") or {}, payload.get("index", len(self.blocks))
                )
                if opened is not None:
                    self.blocks.append(opened)
            case "content_block_delta":
                target = next((b for b in self.blocks if b.index == payload.get("index")), None)
                if target is not None:
                    _apply_delta(target, payload.get("delta") or {})
            case "message_delta":
                delta = payload.get("delta") or {}
                if reason := delta.get("stop_reason"):
                    self.stop = _stop_reason(reason, delta)
                _merge_usage(self.usage, payload.get("usage") or {})


def _open_block(block: Mapping[str, Any], index: int) -> _Open | None:
    match block.get("type"):
        case "text":
            return _Open("text", index, text=block.get("text") or "")
        case "thinking":
            return _Open("thinking", index, text=block.get("thinking") or "")
        case "redacted_thinking":
            return _Open(
                "thinking",
                index,
                text="[Reasoning redacted]",
                signature=block.get("data") or "",
                redacted=True,
            )
        case "tool_use":
            return _Open(
                "tool",
                index,
                call_id=block.get("id") or "",
                name=block.get("name") or "",
                initial_input=block.get("input") or {},
            )
    return None


def _apply_delta(target: _Open, delta: Mapping[str, Any]) -> None:
    match delta.get("type"):
        case "text_delta" if target.kind == "text":
            target.text += delta.get("text", "")
        case "thinking_delta" if target.kind == "thinking":
            target.text += delta.get("thinking", "")
        case "signature_delta" if target.kind == "thinking":
            target.signature += delta.get("signature", "")
        case "input_json_delta" if target.kind == "tool":
            target.partial_json += delta.get("partial_json", "")


def _stop_reason(reason: str, delta: Mapping[str, Any]) -> StopReason:
    if reason in ("refusal", "sensitive"):
        details = delta.get("stop_details") or {}
        raise ModelCallError(
            details.get("explanation") or "The model refused to complete the request",
            error_type=reason,
        )
    if reason not in _STOP:
        raise ModelCallError(f"Unhandled stop reason: {reason}")
    return _STOP[reason]


def _merge_usage(into: dict[str, int], raw: Mapping[str, Any]) -> None:
    for key in (
        "input_tokens",
        "output_tokens",
        "cache_read_input_tokens",
        "cache_creation_input_tokens",
    ):
        value = raw.get(key)
        if isinstance(value, int):
            into[key] = value
    creation = raw.get("cache_creation")
    if isinstance(creation, Mapping) and isinstance(creation.get("ephemeral_1h_input_tokens"), int):
        into["ephemeral_1h_input_tokens"] = creation["ephemeral_1h_input_tokens"]
    details = raw.get("output_tokens_details")
    if isinstance(details, Mapping) and isinstance(details.get("thinking_tokens"), int):
        into["thinking_tokens"] = details["thinking_tokens"]
