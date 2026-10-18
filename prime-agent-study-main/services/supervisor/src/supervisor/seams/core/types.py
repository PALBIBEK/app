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


@dataclass
class AssembledPrompt:
    """Assembled system prompt text.

    ``content_digest`` is optional for test stability only — not a catalog pin.
    """

    text: str
    content_digest: str | None = None


@dataclass(frozen=True)
class ChildId:
    """Branded child handle id (C-7)."""

    value: str

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True)
class JobId:
    """Branded background job id (C-7)."""

    value: str

    def __str__(self) -> str:
        return self.value


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


@dataclass
class ChatMessage:
    """OpenAI-compat message shape.

    An assistant message may carry ``tool_calls``; the ``tool`` message answering
    one carries its ``tool_call_id``. Without both, a model cannot tell which call
    a result belongs to.
    """

    role: str
    content: str
    tool_calls: list[dict[str, Any]] | None = None
    tool_call_id: str | None = None

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


@dataclass
class StreamEvent:
    """OpenAI-compat stream chunk."""

    type: str
    delta: str = ""
    finish_reason: str | None = None
    model: str | None = None
