"""OpenAI-compatible chat completions, as pi-ai speaks it.

The fallback wire for every gateway group that is not purely Anthropic or OpenAI.
Quirks are detected from the provider and endpoint, then overridden by the
model's own compat (a gateway group's come from its advertised parameters).
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Any, Literal

from supervisor.models.messages import (
    AssistantMessage,
    Context,
    IdNormalizer,
    TextBlock,
    ThinkingBlock,
    Tool,
    ToolCallBlock,
    ToolResultMessage,
    UserMessage,
    clamp_max_tokens,
    transform_messages,
)
from supervisor.models.spec import ModelSpec, TokenUsage
from supervisor.models.transports.base import (
    AssistantTurn,
    Body,
    ModelCallError,
    StopReason,
    WireCall,
    merged_headers,
    parse_json,
    parse_partial_json,
    short_hash,
    stream_events,
)

_INVALID_ID_CHARS = re.compile(r"[^a-zA-Z0-9_-]")
_REASONING_FIELDS = ("reasoning_content", "reasoning", "reasoning_text")


@dataclass(frozen=True, slots=True)
class ChatCompat:
    supports_store: bool
    supports_developer_role: bool
    supports_reasoning_effort: bool
    supports_usage_in_streaming: bool
    supports_strict_mode: bool
    supports_long_cache_retention: bool
    send_session_affinity_headers: bool
    max_tokens_field: Literal["max_tokens", "max_completion_tokens"]
    thinking_format: Literal["openai", "openrouter"]
    session_affinity_format: Literal["openai", "openrouter"]
    cache_control_format: Literal["anthropic"] | None = None


def chat_compat(spec: ModelSpec) -> ChatCompat:
    """Detected quirks for the provider and endpoint, overridden by the model's own."""
    openrouter = spec.provider == "openrouter" or "openrouter.ai" in spec.base_url
    detected = ChatCompat(
        supports_store=True,
        supports_developer_role=(
            spec.id.startswith(("anthropic/", "openai/")) if openrouter else True
        ),
        supports_reasoning_effort=True,
        supports_usage_in_streaming=True,
        supports_strict_mode=True,
        supports_long_cache_retention=True,
        send_session_affinity_headers=False,
        max_tokens_field="max_completion_tokens",
        thinking_format="openrouter" if openrouter else "openai",
        session_affinity_format="openrouter" if openrouter else "openai",
        cache_control_format="anthropic"
        if spec.provider == "openrouter" and spec.id.startswith("anthropic/")
        else None,
    )
    c = spec.compat
    overrides: dict[str, Any] = {
        name: value
        for name, value in {
            "supports_store": c.supports_store,
            "supports_developer_role": c.supports_developer_role,
            "supports_reasoning_effort": c.supports_reasoning_effort,
            "supports_usage_in_streaming": c.supports_usage_in_streaming,
            "supports_strict_mode": c.supports_strict_mode,
            "supports_long_cache_retention": c.supports_long_cache_retention,
            "send_session_affinity_headers": c.send_session_affinity_headers,
            "max_tokens_field": c.max_tokens_field,
            "thinking_format": c.thinking_format,
            "session_affinity_format": c.session_affinity_format,
            "cache_control_format": c.cache_control_format,
        }.items()
        if value is not None
    }
    return replace(detected, **overrides)


def normalize_tool_call_id(spec: ModelSpec) -> IdNormalizer:
    def normalize(call_id: str, _source: AssistantMessage) -> str:
        if "|" in call_id:
            call, _, item = call_id.partition("|")
            call = _INVALID_ID_CHARS.sub("_", call)
            item = _INVALID_ID_CHARS.sub("_", item)
            combined = f"{call}_{item}" if item else call
            if len(combined) <= 40:
                return combined
            digest = short_hash(call_id)[:8]
            return f"{call[: max(1, 40 - len(digest) - 1)]}_{digest}"
        if spec.provider == "openai":
            return call_id[:40]
        return call_id

    return normalize


def session_headers(compat: ChatCompat, session_id: str | None) -> dict[str, str]:
    if not session_id or not compat.send_session_affinity_headers:
        return {}
    if compat.session_affinity_format == "openrouter":
        return {"x-session-id": session_id}
    return {
        "session_id": session_id,
        "x-client-request-id": session_id,
        "x-session-affinity": session_id,
    }


def convert_messages(context: Context, spec: ModelSpec, compat: ChatCompat) -> list[Body]:
    params: list[Body] = []
    if context.system_prompt:
        developer = spec.reasoning and compat.supports_developer_role
        params.append(
            {"role": "developer" if developer else "system", "content": context.system_prompt}
        )
    for message in transform_messages(context.messages, spec, normalize_tool_call_id(spec)):
        match message:
            case UserMessage(text, images) if images:
                parts: list[Body] = [{"type": "text", "text": text}]
                parts += [{"type": "image_url", "image_url": {"url": i.data_url}} for i in images]
                params.append({"role": "user", "content": parts})
            case UserMessage(text, _):
                params.append({"role": "user", "content": text})
            case AssistantMessage(blocks):
                text = "".join(
                    b.text for b in blocks if isinstance(b, TextBlock) and b.text.strip()
                )
                entry: Body = {"role": "assistant", "content": text or None}
                thinking = [
                    b for b in blocks if isinstance(b, ThinkingBlock) and b.thinking.strip()
                ]
                if thinking and thinking[0].signature in _REASONING_FIELDS:
                    entry[str(thinking[0].signature)] = "\n".join(b.thinking for b in thinking)
                calls = [b for b in blocks if isinstance(b, ToolCallBlock)]
                if calls:
                    entry["tool_calls"] = [
                        {
                            "id": c.id,
                            "type": "function",
                            "function": {
                                "name": c.name,
                                "arguments": json.dumps(dict(c.arguments)),
                            },
                        }
                        for c in calls
                    ]
                if entry["content"] is None and not calls:
                    continue
                params.append(entry)
            case ToolResultMessage(call_id, _, text, _):
                params.append(
                    {"role": "tool", "content": text or "(no tool output)", "tool_call_id": call_id}
                )
    return params


def convert_tools(tools: tuple[Tool, ...], compat: ChatCompat) -> list[Body]:
    return [
        {
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description,
                "parameters": dict(tool.parameters),
                **({"strict": False} if compat.supports_strict_mode else {}),
            },
        }
        for tool in tools
    ]


def _has_tool_history(context: Context) -> bool:
    return any(
        isinstance(m, ToolResultMessage)
        or (isinstance(m, AssistantMessage) and any(isinstance(b, ToolCallBlock) for b in m.blocks))
        for m in context.messages
    )


def _apply_cache_control(messages: list[Body], tools: list[Body] | None, control: Body) -> None:
    for message in messages:
        if message["role"] in ("system", "developer"):
            _mark_text(message, control)
            break
    if tools:
        tools[-1]["cache_control"] = control
    for message in reversed(messages):
        if message["role"] in ("user", "assistant", "tool") and _mark_text(message, control):
            break


def _mark_text(message: Body, control: Body) -> bool:
    content = message.get("content")
    if isinstance(content, str):
        if not content:
            return False
        message["content"] = [{"type": "text", "text": content, "cache_control": control}]
        return True
    if isinstance(content, list):
        for part in reversed(content):
            if isinstance(part, dict) and part.get("type") == "text":
                part["cache_control"] = control
                return True
    return False


def build_request(call: WireCall) -> tuple[str, dict[str, str], Body]:
    spec, options, context = call.spec, call.options, call.context
    compat = chat_compat(spec)
    retention = options.cache_retention
    headers = merged_headers(
        {"authorization": f"Bearer {call.api_key}"},
        spec.headers,
        session_headers(compat, options.session_id if retention != "none" else None),
        call.headers,
    )
    messages = convert_messages(context, spec, compat)
    long_cache = retention == "long" and compat.supports_long_cache_retention
    body: Body = {"model": spec.id, "messages": messages, "stream": True}
    if options.session_id and (
        ("api.openai.com" in spec.base_url and retention != "none") or long_cache
    ):
        body["prompt_cache_key"] = options.session_id[:64]
    if long_cache:
        body["prompt_cache_retention"] = "24h"
    if compat.supports_usage_in_streaming:
        body["stream_options"] = {"include_usage": True}
    if compat.supports_store:
        body["store"] = False
    body[compat.max_tokens_field] = clamp_max_tokens(
        spec, context, options.max_tokens or spec.max_tokens
    )
    if options.temperature is not None:
        body["temperature"] = options.temperature
    tools: list[Body] | None = None
    if context.tools:
        tools = body["tools"] = convert_tools(context.tools, compat)
    elif _has_tool_history(context):
        body["tools"] = []
    if compat.cache_control_format == "anthropic" and retention != "none":
        ttl = {"ttl": "1h"} if long_cache else {}
        _apply_cache_control(messages, tools, {"type": "ephemeral", **ttl})
    if options.tool_choice is not None:
        body["tool_choice"] = options.tool_choice
    level = options.thinking
    if spec.reasoning and compat.thinking_format == "openrouter":
        if level != "off":
            mapped = spec.thinking_level_map.get(level)
            body["reasoning"] = {"effort": mapped if isinstance(mapped, str) else level}
        elif "off" not in spec.thinking_level_map or spec.thinking_level_map["off"] is not None:
            body["reasoning"] = {"effort": spec.thinking_level_map.get("off") or "none"}
    elif level != "off" and spec.reasoning and compat.supports_reasoning_effort:
        mapped = spec.thinking_level_map.get(level)
        body["reasoning_effort"] = mapped if isinstance(mapped, str) else level
    return f"{spec.base_url}/chat/completions", headers, body


@dataclass(slots=True)
class _ToolCall:
    id: str = ""
    name: str = ""
    arguments: str = ""


_FINISH: Mapping[str, StopReason] = {
    "stop": "stop",
    "end": "stop",
    "length": "length",
    "function_call": "toolUse",
    "tool_calls": "toolUse",
}


def call_completions(call: WireCall) -> AssistantTurn:
    url, headers, body = build_request(call)
    stream = _CompletionStream()
    for event in stream_events(call, url, headers, body):
        if event.data and event.data != "[DONE]":
            chunk = parse_json(event.data, "chat completion")
            if isinstance(chunk, dict):
                stream.feed(chunk)
    if stream.stop is None:
        raise ModelCallError("Stream ended without finish_reason")
    return AssistantTurn(
        blocks=stream.blocks(),
        usage=stream.usage,
        cost=call.spec.pricing.cost(stream.usage),
        stop_reason=stream.stop,
        response_id=stream.response_id,
    )


class _CompletionStream:
    """Folds chat-completion chunks into text, thinking and tool calls, in first-seen order."""

    def __init__(self) -> None:
        self.order: list[str | _ToolCall] = []
        self.text: list[str] = []
        self.thinking: list[str] = []
        self.thinking_field: str | None = None
        self.by_index: dict[int, _ToolCall] = {}
        self.by_id: dict[str, _ToolCall] = {}
        self.usage = TokenUsage()
        self.stop: StopReason | None = None
        self.response_id: str | None = None

    def feed(self, chunk: Mapping[str, Any]) -> None:
        self.response_id = self.response_id or chunk.get("id")
        if isinstance(chunk.get("usage"), dict):
            self.usage = _usage(chunk["usage"])
        choices = chunk.get("choices")
        choice = choices[0] if isinstance(choices, list) and choices else None
        if not isinstance(choice, dict):
            return
        if not isinstance(chunk.get("usage"), dict) and isinstance(choice.get("usage"), dict):
            self.usage = _usage(choice["usage"])
        if reason := choice.get("finish_reason"):
            if reason not in _FINISH:
                raise ModelCallError(f"Provider finish_reason: {reason}")
            self.stop = _FINISH[reason]
        delta = choice.get("delta") or {}
        self._text(delta)
        self._thinking(delta)
        for raw in delta.get("tool_calls") or []:
            self._tool_call(raw)

    def _text(self, delta: Mapping[str, Any]) -> None:
        if isinstance(delta.get("content"), str) and delta["content"]:
            if "text" not in self.order:
                self.order.append("text")
            self.text.append(delta["content"])

    def _thinking(self, delta: Mapping[str, Any]) -> None:
        for name in _REASONING_FIELDS:
            value = delta.get(name)
            if isinstance(value, str) and value:
                if "thinking" not in self.order:
                    self.order.append("thinking")
                    self.thinking_field = name
                self.thinking.append(value)
                return

    def _tool_call(self, raw: Mapping[str, Any]) -> None:
        index = raw.get("index")
        current = self.by_index.get(index) if isinstance(index, int) else None
        if current is None and raw.get("id"):
            current = self.by_id.get(raw["id"])
        if current is None:
            current = _ToolCall()
            self.order.append(current)
            if isinstance(index, int):
                self.by_index[index] = current
        if raw.get("id") and not current.id:
            current.id = raw["id"]
            self.by_id[current.id] = current
        function = raw.get("function") or {}
        if function.get("name") and not current.name:
            current.name = function["name"]
        current.arguments += function.get("arguments") or ""

    def blocks(self) -> tuple[TextBlock | ThinkingBlock | ToolCallBlock, ...]:
        out: list[TextBlock | ThinkingBlock | ToolCallBlock] = []
        for entry in self.order:
            if entry == "text":
                out.append(TextBlock("".join(self.text)))
            elif entry == "thinking":
                out.append(ThinkingBlock("".join(self.thinking), self.thinking_field))
            elif isinstance(entry, _ToolCall):
                out.append(ToolCallBlock(entry.id, entry.name, parse_partial_json(entry.arguments)))
        return tuple(out)


def _usage(raw: Mapping[str, Any]) -> TokenUsage:
    details = raw.get("prompt_tokens_details") or {}
    cache_read = int(details.get("cached_tokens") or raw.get("prompt_cache_hit_tokens") or 0)
    cache_write = int(details.get("cache_write_tokens") or 0)
    prompt = int(raw.get("prompt_tokens") or 0)
    return TokenUsage(
        input=max(0, prompt - cache_read - cache_write),
        output=int(raw.get("completion_tokens") or 0),
        cache_read=cache_read,
        cache_write=cache_write,
        reasoning=int((raw.get("completion_tokens_details") or {}).get("reasoning_tokens") or 0),
    )
