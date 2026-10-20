"""Echo provider — a deterministic, network-free stand-in for every wire.

Echo answers chat completions, Anthropic Messages and OpenAI Responses with the
same conversation semantics, and it is as strict as the real vendors about the
state a client must replay:

* the reply is ``echo:<text>`` for the last user turn or tool result;
* a user turn ``/tool <name> <json-args>`` naming an offered tool produces a tool call,
  and so does a ``tool_choice`` forcing an offered tool (its arguments: the user
  turn, when that is a JSON object);
* Messages requests with thinking enabled get a signed ``thinking`` block, and any
  replayed thinking block whose signature does not verify is rejected (HTTP 400),
  as Anthropic rejects tampered or unsigned thinking;
* Responses requests asking for ``reasoning.encrypted_content`` get a reasoning
  item carrying it, and replayed reasoning without valid encrypted content is
  rejected (HTTP 400); ``service_tier`` is echoed on the response.
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from dataclasses import dataclass
from typing import Any

from supervisor.gateway.providers.base import SSE, Body, Call, UpstreamError, Wire
from supervisor.gateway.wire_codec import sse_from_response

THINKING = "echo is thinking"
_TOOL_COMMAND = "/tool "


def seal(text: str) -> str:
    """The signature echo attaches to a thinking block or reasoning item."""
    return "echo-sig:" + hashlib.sha256(text.encode()).hexdigest()[:24]


@dataclass(frozen=True, slots=True)
class _ToolCall:
    name: str
    arguments: dict[str, Any]
    call_id: str


@dataclass(frozen=True, slots=True)
class _Reply:
    text: str | None
    tool_call: _ToolCall | None
    prompt_tokens: int

    @property
    def output_tokens(self) -> int:
        if self.tool_call is not None:
            return max(1, len(json.dumps(self.tool_call.arguments).split()))
        return max(1, len((self.text or "").split()))


def _words(value: Any) -> int:
    if isinstance(value, str):
        return len(value.split())
    if isinstance(value, dict):
        return sum(
            _words(v) for k, v in value.items() if k not in ("signature", "encrypted_content")
        )
    if isinstance(value, list):
        return sum(_words(v) for v in value)
    return 0


def _text(content: Any) -> str:
    """Plain text of a content field in any wire's spelling."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for part in content:
            if isinstance(part, dict) and isinstance(part.get("text"), str):
                parts.append(part["text"])
            elif isinstance(part, dict) and part.get("type") == "tool_result":
                parts.append(_text(part.get("content")))
        return "\n".join(parts)
    return ""


def _tool_call(text: str, offered: set[str]) -> _ToolCall | None:
    if not text.startswith(_TOOL_COMMAND):
        return None
    name, _, raw = text[len(_TOOL_COMMAND) :].strip().partition(" ")
    if name not in offered:
        return None
    try:
        arguments = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        return None
    if not isinstance(arguments, dict):
        return None
    return _ToolCall(name=name, arguments=arguments, call_id=f"call_echo_{uuid.uuid4().hex[:12]}")


def _forced(choice: Any) -> str | None:
    """The tool a ``tool_choice`` forces, in any wire's spelling."""
    if not isinstance(choice, dict):
        return None
    if isinstance(choice.get("function"), dict):
        name = choice["function"].get("name")
    else:
        name = choice.get("name") if choice.get("type") in ("tool", "function") else None
    return name if isinstance(name, str) else None


def _reply(
    last_text: str,
    *,
    tool_result: bool,
    offered: set[str],
    prompt_tokens: int,
    forced: str | None = None,
) -> _Reply:
    if forced is not None and forced in offered:
        try:
            arguments = json.loads(last_text)
        except json.JSONDecodeError:
            arguments = None
        call_id = f"call_echo_{uuid.uuid4().hex[:12]}"
        return _Reply(
            text=None,
            tool_call=_ToolCall(forced, arguments if isinstance(arguments, dict) else {}, call_id),
            prompt_tokens=prompt_tokens,
        )
    call = None if tool_result else _tool_call(last_text, offered)
    if call is not None:
        return _Reply(text=None, tool_call=call, prompt_tokens=prompt_tokens)
    return _Reply(
        text=f"echo:{last_text}" if last_text else "echo:ok",
        tool_call=None,
        prompt_tokens=prompt_tokens,
    )


def _reject(message: str) -> UpstreamError:
    return UpstreamError(f"echo upstream returned HTTP 400: {message}", status=400)


class EchoChatProvider:
    name = "echo"
    wires: frozenset[Wire] = frozenset(Wire)

    def credential_ok(self) -> bool:
        return True

    def call(self, call: Call) -> Body | SSE:
        build = {
            Wire.CHAT: self._chat,
            Wire.MESSAGES: self._messages,
            Wire.RESPONSES: self._responses,
        }
        response = build[call.wire](call.body)
        return sse_from_response(call.wire, response) if call.stream else response

    # -- chat completions --------------------------------------------------

    def _chat(self, body: Body) -> Body:
        messages = [m for m in body.get("messages") or [] if isinstance(m, dict)]
        last = messages[-1] if messages else {}
        offered = {
            t["function"]["name"]
            for t in body.get("tools") or []
            if isinstance(t, dict)
            and isinstance(t.get("function"), dict)
            and "name" in t["function"]
        }
        reply = _reply(
            _text(last.get("content")),
            tool_result=last.get("role") == "tool",
            offered=offered,
            prompt_tokens=_words(messages),
            forced=_forced(body.get("tool_choice")),
        )
        message: Body = {"role": "assistant", "content": reply.text}
        if reply.tool_call is not None:
            message["tool_calls"] = [
                {
                    "id": reply.tool_call.call_id,
                    "type": "function",
                    "function": {
                        "name": reply.tool_call.name,
                        "arguments": json.dumps(reply.tool_call.arguments),
                    },
                }
            ]
        return {
            "id": f"chatcmpl-echo-{uuid.uuid4().hex[:12]}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": body.get("model"),
            "choices": [
                {
                    "index": 0,
                    "message": message,
                    "finish_reason": "tool_calls" if reply.tool_call else "stop",
                }
            ],
            "usage": {
                "prompt_tokens": reply.prompt_tokens,
                "completion_tokens": reply.output_tokens,
                "total_tokens": reply.prompt_tokens + reply.output_tokens,
            },
        }

    # -- Anthropic Messages ------------------------------------------------

    def _messages(self, body: Body) -> Body:
        messages = [m for m in body.get("messages") or [] if isinstance(m, dict)]
        for message in messages:
            if message.get("role") != "assistant" or not isinstance(message.get("content"), list):
                continue
            for block in message["content"]:
                if (
                    isinstance(block, dict)
                    and block.get("type") == "thinking"
                    and block.get("signature") != seal(block.get("thinking", ""))
                ):
                    raise _reject("thinking block signature is missing or invalid")
        last = messages[-1] if messages else {}
        content = last.get("content")
        tool_result = isinstance(content, list) and any(
            isinstance(b, dict) and b.get("type") == "tool_result" for b in content
        )
        offered = {
            t["name"] for t in body.get("tools") or [] if isinstance(t, dict) and "name" in t
        }
        reply = _reply(
            _text(content),
            tool_result=tool_result,
            offered=offered,
            prompt_tokens=_words(messages) + _words(body.get("system")),
            forced=_forced(body.get("tool_choice")),
        )
        blocks: list[Body] = []
        thinking = body.get("thinking")
        if isinstance(thinking, dict) and thinking.get("type") in ("enabled", "adaptive"):
            blocks.append({"type": "thinking", "thinking": THINKING, "signature": seal(THINKING)})
        if reply.tool_call is not None:
            blocks.append(
                {
                    "type": "tool_use",
                    "id": f"toolu_echo_{uuid.uuid4().hex[:12]}",
                    "name": reply.tool_call.name,
                    "input": reply.tool_call.arguments,
                }
            )
        else:
            blocks.append({"type": "text", "text": reply.text})
        return {
            "id": f"msg_echo_{uuid.uuid4().hex[:12]}",
            "type": "message",
            "role": "assistant",
            "model": body.get("model"),
            "content": blocks,
            "stop_reason": "tool_use" if reply.tool_call else "end_turn",
            "stop_sequence": None,
            "usage": {
                "input_tokens": reply.prompt_tokens,
                "output_tokens": reply.output_tokens,
                "cache_read_input_tokens": 0,
                "cache_creation_input_tokens": 0,
            },
        }

    # -- OpenAI Responses --------------------------------------------------

    def _responses(self, body: Body) -> Body:
        raw_input = body.get("input")
        items: list[Any] = (
            [{"role": "user", "content": raw_input}]
            if isinstance(raw_input, str)
            else list(raw_input or [])
        )
        for item in items:
            if isinstance(item, dict) and item.get("type") == "reasoning":
                summary = _text(item.get("summary"))
                if item.get("encrypted_content") != seal(summary):
                    raise _reject("reasoning item encrypted_content is missing or invalid")
        last = items[-1] if items and isinstance(items[-1], dict) else {}
        tool_result = last.get("type") == "function_call_output"
        last_text = _text(last.get("output") if tool_result else last.get("content"))
        offered = {
            t["name"]
            for t in body.get("tools") or []
            if isinstance(t, dict) and t.get("type") == "function" and "name" in t
        }
        reply = _reply(
            last_text,
            tool_result=tool_result,
            offered=offered,
            prompt_tokens=_words(items) + _words(body.get("instructions")),
            forced=_forced(body.get("tool_choice")),
        )
        output: list[Body] = []
        reasoning = body.get("reasoning")
        include = body.get("include") or []
        if (
            isinstance(reasoning, dict)
            and reasoning.get("effort") not in (None, "none")
            and "reasoning.encrypted_content" in include
        ):
            output.append(
                {
                    "type": "reasoning",
                    "id": f"rs_echo_{uuid.uuid4().hex[:12]}",
                    "summary": [{"type": "summary_text", "text": THINKING}],
                    "encrypted_content": seal(THINKING),
                }
            )
        if reply.tool_call is not None:
            output.append(
                {
                    "type": "function_call",
                    "id": f"fc_echo_{uuid.uuid4().hex[:12]}",
                    "call_id": reply.tool_call.call_id,
                    "name": reply.tool_call.name,
                    "arguments": json.dumps(reply.tool_call.arguments),
                    "status": "completed",
                }
            )
        else:
            output.append(
                {
                    "type": "message",
                    "id": f"msg_echo_{uuid.uuid4().hex[:12]}",
                    "role": "assistant",
                    "status": "completed",
                    "content": [{"type": "output_text", "text": reply.text, "annotations": []}],
                }
            )
        return {
            "id": f"resp_echo_{uuid.uuid4().hex[:12]}",
            "object": "response",
            "created_at": int(time.time()),
            "status": "completed",
            "model": body.get("model"),
            "service_tier": body.get("service_tier") or "default",
            "output": output,
            "usage": {
                "input_tokens": reply.prompt_tokens,
                "input_tokens_details": {"cached_tokens": 0},
                "output_tokens": reply.output_tokens,
                "output_tokens_details": {"reasoning_tokens": 0},
                "total_tokens": reply.prompt_tokens + reply.output_tokens,
            },
        }
