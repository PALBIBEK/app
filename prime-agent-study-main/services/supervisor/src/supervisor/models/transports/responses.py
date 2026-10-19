"""OpenAI Responses, as pi-ai speaks it.

Requests are stateless (``store: false``): reasoning comes back as an item with
``encrypted_content``, which is kept verbatim (as the reasoning part's signature)
and sent back to the same model on the next request. ``service_tier`` — which is
how an Ultrafast selection reaches its base model — is a request field like any
other, and a Flex or Priority tier reprices the call.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from supervisor.models.messages import (
    AssistantMessage,
    Context,
    IdNormalizer,
    Message,
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

MIN_OUTPUT_TOKENS = 16
"""Responses rejects ``max_output_tokens`` below 16."""
PROMPT_CACHE_KEY_MAX = 64
NATIVE_TOOL_CALL_PROVIDERS = frozenset({"openai", "openai-codex", "opencode"})
_INVALID_ID_CHARS = re.compile(r"[^a-zA-Z0-9_-]")


def _id_part(part: str) -> str:
    return _INVALID_ID_CHARS.sub("_", part)[:64].rstrip("_")


def tool_call_id_normalizer(spec: ModelSpec) -> IdNormalizer:
    """Responses tool call ids are ``call_id|item_id``; item ids must start with ``fc``."""

    def normalize(call_id: str, source: AssistantMessage) -> str:
        if spec.provider not in NATIVE_TOOL_CALL_PROVIDERS or "|" not in call_id:
            return _id_part(call_id)
        call, item = call_id.split("|", 1)
        foreign = source.api is not None and source.api is not spec.api
        item_id = f"fc_{short_hash(item)}"[:64] if foreign else _id_part(item)
        if not item_id.startswith("fc_"):
            item_id = _id_part(f"fc_{item_id}")
        return f"{_id_part(call)}|{item_id}"

    return normalize


def encode_text_signature(item_id: str, phase: str | None) -> str:
    return json.dumps({"v": 1, "id": item_id, **({"phase": phase} if phase else {})})


def parse_text_signature(signature: str | None) -> tuple[str, str | None] | None:
    if not signature:
        return None
    if signature.startswith("{"):
        try:
            parsed = json.loads(signature)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, dict) and parsed.get("v") == 1 and isinstance(parsed.get("id"), str):
            phase = parsed.get("phase")
            return parsed["id"], phase if phase in ("commentary", "final_answer") else None
    return signature, None


def session_headers(spec: ModelSpec, session_id: str | None) -> dict[str, str]:
    if not session_id:
        return {}
    openrouter = spec.provider == "openrouter" or "openrouter.ai" in spec.base_url
    if (
        spec.compat.session_affinity_format or ("openrouter" if openrouter else "openai")
    ) == "openrouter":
        return {"x-session-id": session_id}
    return {"session_id": session_id, "x-client-request-id": session_id}


def convert_input(context: Context, spec: ModelSpec) -> list[Body]:
    messages: list[Message] = transform_messages(
        context.messages, spec, tool_call_id_normalizer(spec)
    )
    items: list[Body] = []
    if context.system_prompt:
        developer = spec.reasoning and spec.compat.supports_developer_role is not False
        items.append(
            {"role": "developer" if developer else "system", "content": context.system_prompt}
        )
    for index, message in enumerate(messages):
        match message:
            case UserMessage(text, images):
                content: list[Body] = [{"type": "input_text", "text": text}]
                content += [
                    {"type": "input_image", "detail": "auto", "image_url": i.data_url}
                    for i in images
                ]
                items.append({"role": "user", "content": content})
            case AssistantMessage(blocks, model, api, _):
                different_model = model != spec.id and api in (None, spec.api)
                text_index = 0
                for block in blocks:
                    match block:
                        case ThinkingBlock(_, signature, _):
                            if signature:
                                items.append(json.loads(signature))
                        case TextBlock(text, signature):
                            parsed = parse_text_signature(signature)
                            fallback = (
                                f"msg_pi_{index}"
                                if text_index == 0
                                else f"msg_pi_{index}_{text_index}"
                            )
                            text_index += 1
                            item_id, phase = parsed if parsed else (fallback, None)
                            if len(item_id) > 64:
                                item_id = f"msg_{short_hash(item_id)}"
                            items.append(
                                {
                                    "type": "message",
                                    "role": "assistant",
                                    "content": [
                                        {"type": "output_text", "text": text, "annotations": []}
                                    ],
                                    "status": "completed",
                                    "id": item_id,
                                    **({"phase": phase} if phase else {}),
                                }
                            )
                        case ToolCallBlock(call_id, name, arguments):
                            call, _, item = call_id.partition("|")
                            item_ref: str | None = item or None
                            if item_ref is not None and (
                                (different_model and item_ref.startswith("fc_"))
                                or not item_ref.startswith("fc_")
                            ):
                                item_ref = None
                            items.append(
                                {
                                    "type": "function_call",
                                    **({"id": item_ref} if item_ref else {}),
                                    "call_id": call,
                                    "name": name,
                                    "arguments": json.dumps(dict(arguments)),
                                }
                            )
            case ToolResultMessage(call_id, _, text, _):
                items.append(
                    {
                        "type": "function_call_output",
                        "call_id": call_id.split("|", 1)[0],
                        "output": text or "(no tool output)",
                    }
                )
    return items


def convert_tools(tools: tuple[Tool, ...], spec: ModelSpec) -> list[Body]:
    strict = spec.compat.supports_strict_mode is True
    return [
        {
            "type": "function",
            "name": tool.name,
            "description": tool.description,
            "parameters": dict(tool.parameters),
            **({"strict": False} if strict else {}),
        }
        for tool in tools
    ]


def build_request(call: WireCall) -> tuple[str, dict[str, str], Body]:
    spec, options, context = call.spec, call.options, call.context
    cached = options.cache_retention != "none"
    headers = merged_headers(
        {"authorization": f"Bearer {call.api_key}"},
        spec.headers,
        session_headers(spec, options.session_id if cached else None),
        call.headers,
    )
    max_tokens = clamp_max_tokens(spec, context, options.max_tokens or spec.max_tokens)
    body: Body = {
        "model": spec.id,
        "input": convert_input(context, spec),
        "stream": True,
        "store": False,
        "max_output_tokens": max(max_tokens, MIN_OUTPUT_TOKENS),
    }
    if cached and options.session_id:
        body["prompt_cache_key"] = options.session_id[:PROMPT_CACHE_KEY_MAX]
    if options.cache_retention == "long" and spec.compat.supports_long_cache_retention is not False:
        body["prompt_cache_retention"] = "24h"
    if options.temperature is not None:
        body["temperature"] = options.temperature
    if options.service_tier is not None:
        body["service_tier"] = options.service_tier
    if context.tools:
        body["tools"] = convert_tools(context.tools, spec)
    if options.tool_choice is not None:
        body["tool_choice"] = options.tool_choice
    if spec.reasoning:
        level = options.thinking
        if level != "off":
            mapped = spec.thinking_level_map.get(level)
            body["reasoning"] = {
                "effort": mapped if isinstance(mapped, str) else level,
                "summary": "auto",
            }
            body["include"] = ["reasoning.encrypted_content"]
        elif "off" not in spec.thinking_level_map or spec.thinking_level_map["off"] is not None:
            body["reasoning"] = {"effort": spec.thinking_level_map.get("off") or "none"}
    return f"{spec.base_url}/responses", headers, body


def service_tier_multiplier(spec: ModelSpec, tier: str | None) -> float:
    if tier == "flex":
        return 0.5
    if tier == "priority":
        return 2.5 if spec.id == "gpt-5.5" else 2.0
    return 1.0


@dataclass(slots=True)
class _Slot:
    kind: str
    text: str = ""
    signature: str | None = None
    call_id: str = ""
    name: str = ""
    partial_json: str = ""
    item_id: str | None = None


_STATUS: Mapping[str, StopReason] = {
    "completed": "stop",
    "incomplete": "length",
    "in_progress": "stop",
    "queued": "stop",
}


def call_responses(call: WireCall) -> AssistantTurn:
    url, headers, body = build_request(call)
    order: list[_Slot] = []
    open_slots: dict[int, _Slot] = {}
    reasoning_by_id: dict[str, _Slot] = {}
    final: Body | None = None
    response_id: str | None = None
    for event in stream_events(call, url, headers, body):
        if not event.data or event.data == "[DONE]":
            continue
        payload = parse_json(event.data, "Responses")
        kind = payload.get("type")
        index = payload.get("output_index")
        slot = open_slots.get(index) if isinstance(index, int) else None
        if kind == "response.created":
            response_id = (payload.get("response") or {}).get("id")
        elif kind == "response.output_item.added" and isinstance(index, int):
            created = _slot(payload.get("item") or {})
            if created is not None:
                open_slots[index] = created
                order.append(created)
        elif kind in ("response.reasoning_summary_text.delta", "response.reasoning_text.delta"):
            if slot is not None and slot.kind == "thinking":
                slot.text += payload.get("delta", "")
        elif kind == "response.reasoning_summary_part.done":
            if slot is not None and slot.kind == "thinking":
                slot.text += "\n\n"
        elif kind in ("response.output_text.delta", "response.refusal.delta"):
            if slot is not None and slot.kind == "text":
                slot.text += payload.get("delta", "")
        elif kind == "response.function_call_arguments.delta":
            if slot is not None and slot.kind == "tool":
                slot.partial_json += payload.get("delta", "")
        elif kind == "response.function_call_arguments.done":
            if slot is not None and slot.kind == "tool":
                slot.partial_json = payload.get("arguments", slot.partial_json)
        elif kind == "response.output_item.done" and isinstance(index, int):
            item = payload.get("item") or {}
            if slot is None:
                slot = _slot(item)
                if slot is None:
                    continue
                order.append(slot)
            _finish(slot, item, reasoning_by_id)
            open_slots.pop(index, None)
        elif kind in ("response.completed", "response.incomplete"):
            final = payload.get("response") or {}
        elif kind == "error":
            raise ModelCallError(f"Error Code {payload.get('code')}: {payload.get('message')}")
        elif kind == "response.failed":
            response = payload.get("response") or {}
            error = response.get("error") or {}
            details = response.get("incomplete_details") or {}
            message = (
                f"{error.get('code') or 'unknown'}: {error.get('message') or 'no message'}"
                if error
                else f"incomplete: {details['reason']}"
                if details.get("reason")
                else "Unknown error (no error details in response)"
            )
            raise ModelCallError(message)
    if final is None:
        raise ModelCallError("OpenAI Responses stream ended before a terminal response event")
    for item in final.get("output") or []:
        if item.get("type") == "reasoning" and item.get("encrypted_content"):
            target = reasoning_by_id.get(item.get("id"))
            if target is not None and target.signature:
                stored = json.loads(target.signature)
                if not stored.get("encrypted_content"):
                    target.signature = json.dumps(
                        {**stored, "encrypted_content": item["encrypted_content"]}
                    )
    usage = _usage(final.get("usage") or {})
    status = final.get("status")
    if status in ("failed", "cancelled"):
        raise ModelCallError(f"Responses returned status {status}")
    stop: StopReason = _STATUS.get(str(status), "stop") if status else "stop"
    blocks = tuple(_block(s) for s in order)
    if stop == "stop" and any(isinstance(b, ToolCallBlock) for b in blocks):
        stop = "toolUse"
    tier = final.get("service_tier") or call.options.service_tier
    cost = call.spec.pricing.cost(usage).scaled(service_tier_multiplier(call.spec, tier))
    return AssistantTurn(
        blocks=blocks,
        usage=usage,
        cost=cost,
        stop_reason=stop,
        response_id=final.get("id") or response_id,
        service_tier=tier,
    )


def _slot(item: Mapping[str, Any]) -> _Slot | None:
    match item.get("type"):
        case "reasoning":
            return _Slot("thinking")
        case "message":
            return _Slot("text")
        case "function_call":
            return _Slot(
                "tool",
                call_id=f"{item.get('call_id')}|{item.get('id')}",
                name=item.get("name") or "",
                partial_json=item.get("arguments") or "",
            )
    return None


def _finish(slot: _Slot, item: Mapping[str, Any], reasoning_by_id: dict[str, _Slot]) -> None:
    if slot.kind == "thinking" and item.get("type") == "reasoning":
        summary = "\n\n".join(s.get("text", "") for s in item.get("summary") or [])
        content = "\n\n".join(c.get("text", "") for c in item.get("content") or [])
        slot.text = summary or content or slot.text
        slot.signature = json.dumps(item)
        if isinstance(item.get("id"), str):
            reasoning_by_id[item["id"]] = slot
    elif slot.kind == "text" and item.get("type") == "message":
        slot.text = "".join(
            (c.get("text") if c.get("type") == "output_text" else c.get("refusal")) or ""
            for c in item.get("content") or []
        )
        slot.signature = encode_text_signature(str(item.get("id")), item.get("phase"))
    elif slot.kind == "tool" and item.get("type") == "function_call":
        slot.partial_json = item.get("arguments") or slot.partial_json or "{}"


def _block(slot: _Slot) -> TextBlock | ThinkingBlock | ToolCallBlock:
    if slot.kind == "thinking":
        return ThinkingBlock(slot.text, slot.signature)
    if slot.kind == "text":
        return TextBlock(slot.text, slot.signature)
    return ToolCallBlock(slot.call_id, slot.name, parse_partial_json(slot.partial_json))


def _usage(raw: Mapping[str, Any]) -> TokenUsage:
    details = raw.get("input_tokens_details") or {}
    cached = int(details.get("cached_tokens") or 0)
    written = int(details.get("cache_write_tokens") or 0)
    return TokenUsage(
        input=max(0, int(raw.get("input_tokens") or 0) - cached - written),
        output=int(raw.get("output_tokens") or 0),
        cache_read=cached,
        cache_write=written,
        reasoning=int((raw.get("output_tokens_details") or {}).get("reasoning_tokens") or 0),
    )
