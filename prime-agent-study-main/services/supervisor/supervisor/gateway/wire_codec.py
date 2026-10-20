"""Per-wire knowledge the gateway needs without translating payloads.

The gateway relays bodies verbatim, but it still has to read two things out of
every wire: the token usage it meters, and — for the echo provider — how a
finished response is spelled as a server-sent event stream.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from typing import Any

from supervisor.gateway.providers.base import Body, Wire


@dataclass(frozen=True, slots=True)
class TokenUsage:
    """Usage in one shape: ``input`` excludes cache reads and writes."""

    input: int = 0
    output: int = 0
    cache_read: int = 0
    cache_write: int = 0
    upstream_cost_usd: float | None = None
    """The upstream's own cost figure when it reports one (OpenRouter does)."""

    @property
    def prompt_tokens(self) -> int:
        return self.input + self.cache_read + self.cache_write


def _int(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else 0


def _cost(usage: Mapping[str, Any]) -> float | None:
    for key in ("cost_total", "cost"):
        value = usage.get(key)
        if isinstance(value, int | float) and not isinstance(value, bool) and value >= 0:
            return float(value)
    return None


def usage_from(wire: Wire, usage: Any) -> TokenUsage | None:
    """Normalize a wire's ``usage`` object; ``None`` when absent."""
    if not isinstance(usage, Mapping):
        return None
    match wire:
        case Wire.CHAT:
            details = usage.get("prompt_tokens_details") or {}
            cached = _int(details.get("cached_tokens")) if isinstance(details, Mapping) else 0
            written = _int(details.get("cache_write_tokens")) if isinstance(details, Mapping) else 0
            prompt = _int(usage.get("prompt_tokens") or usage.get("input_tokens"))
            return TokenUsage(
                input=max(0, prompt - cached - written),
                output=_int(usage.get("completion_tokens") or usage.get("output_tokens")),
                cache_read=cached,
                cache_write=written,
                upstream_cost_usd=_cost(usage),
            )
        case Wire.MESSAGES:
            return TokenUsage(
                input=_int(usage.get("input_tokens")),
                output=_int(usage.get("output_tokens")),
                cache_read=_int(usage.get("cache_read_input_tokens")),
                cache_write=_int(usage.get("cache_creation_input_tokens")),
                upstream_cost_usd=_cost(usage),
            )
        case Wire.RESPONSES:
            details = usage.get("input_tokens_details") or {}
            cached = _int(details.get("cached_tokens")) if isinstance(details, Mapping) else 0
            written = _int(details.get("cache_write_tokens")) if isinstance(details, Mapping) else 0
            return TokenUsage(
                input=max(0, _int(usage.get("input_tokens")) - cached - written),
                output=_int(usage.get("output_tokens")),
                cache_read=cached,
                cache_write=written,
                upstream_cost_usd=_cost(usage),
            )


def response_usage(wire: Wire, body: Body) -> TokenUsage | None:
    return usage_from(wire, body.get("usage"))


def encode_sse(data: Body | str, *, event: str | None = None) -> bytes:
    payload = data if isinstance(data, str) else json.dumps(data, separators=(",", ":"))
    head = f"event: {event}\n" if event else ""
    return f"{head}data: {payload}\n\n".encode()


def iter_sse_data(chunks: Iterable[bytes]) -> Iterator[str]:
    """``data`` payloads of an SSE byte stream, one per event, across chunk boundaries."""
    buffer = ""
    data: list[str] = []
    for chunk in chunks:
        buffer += chunk.decode("utf-8", errors="replace")
        *lines, buffer = buffer.replace("\r\n", "\n").replace("\r", "\n").split("\n")
        for line in lines:
            if line == "":
                if data:
                    yield "\n".join(data)
                    data = []
            elif line.startswith("data:"):
                data.append(line[5:].removeprefix(" "))
    if buffer.startswith("data:"):
        data.append(buffer[5:].removeprefix(" "))
    if data:
        yield "\n".join(data)


class UsageTap:
    """Watches a relayed SSE stream and keeps the latest usage each wire reports.

    Messages reports input usage on ``message_start`` and the final output count
    on ``message_delta``; Responses reports everything on its terminal event; chat
    completions reports usage on the last chunk when ``include_usage`` is set.
    """

    __slots__ = ("_fields", "_pending", "_wire")

    def __init__(self, wire: Wire) -> None:
        self._wire = wire
        self._fields: dict[str, Any] = {}
        self._pending = b""

    def feed(self, chunk: bytes) -> None:
        self._pending += chunk.replace(b"\r\n", b"\n")
        head, sep, tail = self._pending.rpartition(b"\n\n")
        if not sep:
            return
        self._pending = tail
        for payload in iter_sse_data([head + sep]):
            self._observe(payload)

    def _observe(self, payload: str) -> None:
        if payload == "[DONE]" or '"usage"' not in payload:
            return
        try:
            event = json.loads(payload)
        except json.JSONDecodeError:
            return
        if not isinstance(event, dict):
            return
        usage: Any
        match self._wire:
            case Wire.MESSAGES:
                started = event.get("type") == "message_start"
                usage = (event.get("message") or {}).get("usage") if started else event.get("usage")
            case Wire.RESPONSES:
                usage = (event.get("response") or {}).get("usage")
            case Wire.CHAT:
                usage = event.get("usage")
        if isinstance(usage, Mapping):
            self._fields.update({k: v for k, v in usage.items() if v is not None})

    def usage(self) -> TokenUsage | None:
        if self._pending:
            for payload in iter_sse_data([self._pending]):
                self._observe(payload)
            self._pending = b""
        return usage_from(self._wire, self._fields) if self._fields else None


def sse_from_response(wire: Wire, response: Body) -> Iterator[bytes]:
    """Spell a finished response as the event stream its wire would have sent."""
    match wire:
        case Wire.CHAT:
            return _chat_events(response)
        case Wire.MESSAGES:
            return _messages_events(response)
        case Wire.RESPONSES:
            return _responses_events(response)


def _chat_events(response: Body) -> Iterator[bytes]:
    head: Body = {k: response[k] for k in ("id", "created", "model") if k in response}
    head["object"] = "chat.completion.chunk"
    choice = response["choices"][0]
    message = choice["message"]
    delta: Body = {"role": "assistant"}
    if message.get("content"):
        delta["content"] = message["content"]
    if message.get("tool_calls"):
        delta["tool_calls"] = [{"index": i, **call} for i, call in enumerate(message["tool_calls"])]
    yield encode_sse({**head, "choices": [{"index": 0, "delta": delta, "finish_reason": None}]})
    final: Body = {
        **head,
        "choices": [{"index": 0, "delta": {}, "finish_reason": choice["finish_reason"]}],
    }
    if "usage" in response:
        final["usage"] = response["usage"]
    yield encode_sse(final)
    yield encode_sse("[DONE]")


def _messages_events(response: Body) -> Iterator[bytes]:
    usage = dict(response.get("usage") or {})
    start = {**response, "content": [], "stop_reason": None, "usage": {**usage, "output_tokens": 0}}
    yield encode_sse({"type": "message_start", "message": start}, event="message_start")
    for index, block in enumerate(response.get("content") or []):
        kind = block.get("type")
        opened: Body
        deltas: list[Body]
        match kind:
            case "thinking":
                opened = {"type": "thinking", "thinking": "", "signature": ""}
                deltas = [
                    {"type": "thinking_delta", "thinking": block.get("thinking", "")},
                    {"type": "signature_delta", "signature": block.get("signature", "")},
                ]
            case "tool_use":
                opened = {**block, "input": {}}
                deltas = [
                    {
                        "type": "input_json_delta",
                        "partial_json": json.dumps(block.get("input") or {}),
                    }
                ]
            case "text":
                opened = {"type": "text", "text": ""}
                deltas = [{"type": "text_delta", "text": block.get("text", "")}]
            case _:
                opened, deltas = block, []
        yield encode_sse(
            {"type": "content_block_start", "index": index, "content_block": opened},
            event="content_block_start",
        )
        for delta in deltas:
            yield encode_sse(
                {"type": "content_block_delta", "index": index, "delta": delta},
                event="content_block_delta",
            )
        yield encode_sse({"type": "content_block_stop", "index": index}, event="content_block_stop")
    yield encode_sse(
        {
            "type": "message_delta",
            "delta": {"stop_reason": response.get("stop_reason"), "stop_sequence": None},
            "usage": usage,
        },
        event="message_delta",
    )
    yield encode_sse({"type": "message_stop"}, event="message_stop")


def _responses_events(response: Body) -> Iterator[bytes]:
    sequence = iter(range(1 << 30))

    def event(kind: str, **fields: Any) -> bytes:
        return encode_sse({"type": kind, "sequence_number": next(sequence), **fields}, event=kind)

    pending = {**response, "status": "in_progress", "output": [], "usage": None}
    yield event("response.created", response=pending)
    for index, item in enumerate(response.get("output") or []):
        kind = item.get("type")
        if kind == "message":
            yield event(
                "response.output_item.added", output_index=index, item={**item, "content": []}
            )
            for part in item.get("content") or []:
                yield event(
                    "response.output_text.delta",
                    output_index=index,
                    item_id=item.get("id"),
                    content_index=0,
                    delta=part.get("text", ""),
                )
        elif kind == "reasoning":
            yield event(
                "response.output_item.added", output_index=index, item={**item, "summary": []}
            )
            for summary_index, part in enumerate(item.get("summary") or []):
                yield event(
                    "response.reasoning_summary_text.delta",
                    output_index=index,
                    item_id=item.get("id"),
                    summary_index=summary_index,
                    delta=part.get("text", ""),
                )
        elif kind == "function_call":
            yield event(
                "response.output_item.added", output_index=index, item={**item, "arguments": ""}
            )
            yield event(
                "response.function_call_arguments.delta",
                output_index=index,
                item_id=item.get("id"),
                delta=item.get("arguments", ""),
            )
            yield event(
                "response.function_call_arguments.done",
                output_index=index,
                item_id=item.get("id"),
                arguments=item.get("arguments", ""),
            )
        else:
            yield event("response.output_item.added", output_index=index, item=item)
        yield event("response.output_item.done", output_index=index, item=item)
    terminal = (
        "response.completed" if response.get("status") == "completed" else "response.incomplete"
    )
    yield event(terminal, response=response)
