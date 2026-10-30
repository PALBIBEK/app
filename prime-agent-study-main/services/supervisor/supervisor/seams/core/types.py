"""Harness-neutral DTOs for capability seams (no external harness imports)."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ToolDescriptor:
    """Wire-facing tool metadata (name + schema + short description)."""

    name: str
    description: str
    parameters: dict[str, Any]


@dataclass(frozen=True)
class ToolInvokeContext:
    """Opaque handles only — no provider concrete types."""

    trace_id: str = ""
    sandbox_token: str | None = None
    approval_token: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ToolResult:
    """Outcome of a tool invoke (process must not crash on unknown tool)."""

    ok: bool
    payload: Any = None
    error: str | None = None
    code: str | None = None


@dataclass
class PromptBindings:
    """Inputs for assembling a system prompt (pack pin comes in catalog-pins)."""

    cwd: str = "."
    tools: list[Any] | None = None
    skill_listing: str = ""
    extra: str = ""
    custom: str = ""
    # The pack's persona is a short role label ("coder") for lists and builders; a
    # runtime whose prompt starts with the pack's own text leaves it out.
    include_persona: bool = True


@dataclass
class AssembledPrompt:
    """Assembled system prompt text.

    ``content_digest`` is optional for test stability only — not a catalog pin.
    """

    text: str
    content_digest: str | None = None


@dataclass
class SubagentRequest:
    """Provider-neutral spawn / fork / background / continuable request."""

    prompt: str
    description: str = ""
    parent_depth: int = 0
    mode: str = "spawn"
    run_id: str | None = None
    tool_filter: list[str] | None = None
    persona: str | None = None
    depth_limit: int | None = None
    output_schema: dict[str, Any] | None = None


@dataclass
class SubagentRunResult:
    """One-shot / fork result (harness-neutral)."""

    text: str = ""
    child_id: str | None = None
    status: str = "ok"
    meta: dict[str, Any] = field(default_factory=dict)
    spent: dict[str, Any] = field(default_factory=dict)
    """What its model calls through the session's gateway cost (``input_tokens``,
    ``output_tokens``, ``cost``); empty when the child pays its own way (ACP)."""


@dataclass
class JobHandle:
    job_id: str


@dataclass
class ChildHandle:
    child_id: str
    message_id: str = ""


@dataclass
class ChildInfo:
    id: str
    status: str
    description: str = ""
    pending: int = 0
    live: bool = False


class ModelGatewayError(RuntimeError):
    """A model call the gateway (or the provider behind it) refused, with its HTTP status.

    ``retry_after``: seconds the provider asked the caller to wait (``Retry-After``),
    when it said. The message keeps the gateway's wording, so text matching still works.
    """

    def __init__(self, status: int, body: str, retry_after: float | None = None) -> None:
        super().__init__(f"model gateway error {status}: {body}")
        self.status = status
        self.retry_after = retry_after


INVALID_ARGUMENTS_KEY = "__invalid_json__"
"""Key of the one entry a tool call's arguments hold when the model's JSON did not
parse: the raw text, kept so the loop can refuse the call and say why, rather than
run the tool with arguments it never sent."""


def invalid_arguments(raw: object) -> dict[str, Any]:
    """Arguments for a call whose JSON did not parse (or was not an object)."""
    return {INVALID_ARGUMENTS_KEY: str(raw)[:2000]}


@dataclass
class ChatMessage:
    """OpenAI-compat message shape.

    An assistant message may carry ``tool_calls``; the ``tool`` message answering
    one carries its ``tool_call_id``. Without both, a model cannot tell which call
    a result belongs to.

    An assistant message produced by a native-protocol model also carries
    ``parts`` — its ordered ``text`` / ``reasoning`` / ``tool_call`` parts, with the
    provider's signed or encrypted reasoning — and the ``model`` that produced it,
    so that model can be shown its own reasoning again. Chat-completions callers
    read only ``content`` and ``tool_calls``.
    """

    role: str
    content: str
    tool_calls: list[dict[str, Any]] | None = None
    tool_call_id: str | None = None
    parts: list[dict[str, Any]] | None = None
    model: str | None = None
    # A tool result that failed: providers with a field for it (Anthropic's is_error)
    # are told, as pi tells them; chat completions has none and reads the text.
    is_error: bool = False

    def to_wire(self) -> dict[str, Any]:
        """OpenAI chat-completions message. Tool calls may be ``{id, name, arguments}``."""
        wire: dict[str, Any] = {"role": self.role, "content": self.content}
        if self.tool_calls:
            wire["tool_calls"] = [_tool_call_to_wire(call) for call in self.tool_calls]
        if self.tool_call_id is not None:
            wire["tool_call_id"] = self.tool_call_id
        return wire


def _tool_call_to_wire(call: dict[str, Any]) -> dict[str, Any]:
    if "function" in call:  # already in wire form
        return call
    args = call.get("arguments")
    return {
        "id": call.get("id"),
        "type": "function",
        "function": {
            "name": call.get("name"),
            "arguments": args if isinstance(args, str) else json.dumps(args or {}),
        },
    }


@dataclass
class Completion:
    """OpenAI-compat completion result."""

    message: ChatMessage
    model: str | None = None
    finish_reason: str | None = None
    usage: dict[str, Any] | None = None
    tool_calls: list[dict[str, Any]] | None = None

    @property
    def spent(self) -> dict[str, Any]:
        """What the call cost: ``input_tokens``, ``output_tokens`` and ``cost`` (USD)."""
        usage = self.usage or {}
        return {
            "input_tokens": int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0),
            "output_tokens": int(usage.get("completion_tokens") or usage.get("output_tokens") or 0),
            "cost": float(usage.get("cost_total") or usage.get("cost") or 0.0),
        }


@dataclass
class StreamEvent:
    """OpenAI-compat stream chunk."""

    type: str
    delta: str = ""
    finish_reason: str | None = None
    model: str | None = None
