"""Canonical event vocabulary. All runtimes emit these types."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Union


class EventCategory(Enum):
    LIFECYCLE = "lifecycle"
    MESSAGE = "message"
    TOOL = "tool"
    APPROVAL = "approval"
    BACKGROUND = "background"
    CONTEXT = "context"
    META = "meta"


class StopReason(Enum):
    END_TURN = "end_turn"
    TOOL_USE = "tool_use"
    MAX_TOKENS = "max_tokens"
    ABORTED = "aborted"
    ERROR = "error"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"
    DEFERRED = "deferred"


# ── Usage Metrics ──────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Usage:
    """Token usage metrics."""
    input_tokens: int
    output_tokens: int
    cache_read: int = 0
    cache_write: int = 0
    cost_total: float = 0.0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    def __add__(self, other: Usage) -> Usage:
        return Usage(
            self.input_tokens + other.input_tokens,
            self.output_tokens + other.output_tokens,
            self.cache_read + other.cache_read,
            self.cache_write + other.cache_write,
            self.cost_total + other.cost_total,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cache_read": self.cache_read,
            "cache_write": self.cache_write,
            "cost_total": self.cost_total,
            "total_tokens": self.total_tokens,
        }


ZERO_USAGE = Usage(0, 0)


# ── Lifecycle Events ───────────────────────────────────────────────────────────


@dataclass(frozen=True)
class RunStart:
    category: EventCategory = field(default=EventCategory.LIFECYCLE, repr=False)


@dataclass(frozen=True)
class RunEnd:
    messages: list[dict[str, Any]]
    total_usage: Usage | None = None
    category: EventCategory = field(default=EventCategory.LIFECYCLE, repr=False)


@dataclass(frozen=True)
class TurnStart:
    category: EventCategory = field(default=EventCategory.LIFECYCLE, repr=False)


@dataclass(frozen=True)
class TurnEnd:
    stop_reason: str
    category: EventCategory = field(default=EventCategory.LIFECYCLE, repr=False)


# ── Message Events ─────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class MessageEnd:
    role: str
    content: list[dict[str, Any]]
    usage: Usage | None = None
    model: str | None = None
    finish_reason: str | None = None
    category: EventCategory = field(default=EventCategory.MESSAGE, repr=False)


# ── Tool Events ────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ToolStart:
    tool_call_id: str
    tool_name: str
    args: dict[str, Any]
    category: EventCategory = field(default=EventCategory.TOOL, repr=False)


@dataclass(frozen=True)
class ToolEnd:
    tool_call_id: str
    tool_name: str
    result: dict[str, Any]
    is_error: bool = False
    duration_ms: int | None = None
    category: EventCategory = field(default=EventCategory.TOOL, repr=False)


# ── Approval Events ────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ApprovalRequired:
    approval_id: str
    prompt: str
    options: list[str]
    timeout_s: float
    harness_request_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    category: EventCategory = field(default=EventCategory.APPROVAL, repr=False)


@dataclass(frozen=True)
class ApprovalResolved:
    approval_id: str
    choice: str
    status: str  # responded | timed_out | cancelled
    responded_by: str | None = None
    category: EventCategory = field(default=EventCategory.APPROVAL, repr=False)


# ── Background Task Events ─────────────────────────────────────────────────────


@dataclass(frozen=True)
class SubagentStart:
    run_id: str
    child_session_id: str
    parent_session_id: str
    mode: str  # one_shot | fork | continuable | background
    provider: str
    label: str | None = None
    category: EventCategory = field(default=EventCategory.BACKGROUND, repr=False)


@dataclass(frozen=True)
class SubagentEnd:
    run_id: str
    stop_reason: str
    output: str | None = None
    category: EventCategory = field(default=EventCategory.BACKGROUND, repr=False)


@dataclass(frozen=True)
class JobStart:
    job_id: str
    kind: str
    command: str | None = None
    category: EventCategory = field(default=EventCategory.BACKGROUND, repr=False)


@dataclass(frozen=True)
class JobEnd:
    job_id: str
    stop_reason: str
    output: str | None = None
    exit_code: int | None = None
    category: EventCategory = field(default=EventCategory.BACKGROUND, repr=False)


# ── Context Management Events ──────────────────────────────────────────────────


@dataclass(frozen=True)
class CompactionStart:
    compaction_id: str
    reason: str  # manual | threshold | overflow
    tokens_before: int
    first_kept_entry_id: str | None = None
    category: EventCategory = field(default=EventCategory.CONTEXT, repr=False)


@dataclass(frozen=True)
class CompactionEnd:
    compaction_id: str
    stop_reason: str  # ok | error | cancelled | deferred
    tokens_after: int | None = None
    summary_digest: str | None = None
    category: EventCategory = field(default=EventCategory.CONTEXT, repr=False)


@dataclass(frozen=True)
class MemoryRecall:
    block_id: str
    source: str  # semantic | branch | custom
    token_estimate: int
    injected: bool
    relevance_score: float | None = None
    category: EventCategory = field(default=EventCategory.CONTEXT, repr=False)


# ── Meta Events ────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Settled:
    category: EventCategory = field(default=EventCategory.META, repr=False)


@dataclass(frozen=True)
class ErrorEvent:
    error_class: str
    retryable: bool
    detail: str
    error_code: str | None = None
    category: EventCategory = field(default=EventCategory.META, repr=False)


# ── Union & Envelope ───────────────────────────────────────────────────────────


AgentEvent = Union[
    RunStart, RunEnd, TurnStart, TurnEnd,
    MessageEnd,
    ToolStart, ToolEnd,
    ApprovalRequired, ApprovalResolved,
    SubagentStart, SubagentEnd, JobStart, JobEnd,
    CompactionStart, CompactionEnd, MemoryRecall,
    Settled, ErrorEvent,
]


@dataclass(frozen=True)
class Envelope:
    """Event envelope with session/seq/turn/trace metadata."""
    session_id: str
    seq: int
    event_id: str
    ts_ms: int
    turn_id: str | None
    trace_id: str | None
    payload: AgentEvent

    @property
    def event_type(self) -> str:
        return type(self.payload).__name__

    @property
    def category(self) -> EventCategory:
        return self.payload.category  # type: ignore

    def to_dict(self) -> dict[str, Any]:
        payload_dict = {}
        for k, v in self.payload.__dict__.items():
            if k == "category":
                continue
            payload_dict[k] = v.to_dict() if hasattr(v, "to_dict") else (v.value if isinstance(v, Enum) else v)

        return {
            "session_id": self.session_id,
            "seq": self.seq,
            "event_id": self.event_id,
            "ts_ms": self.ts_ms,
            "turn_id": self.turn_id,
            "trace_id": self.trace_id,
            "event_type": self.event_type,
            "category": self.category.value,
            "payload": payload_dict,
        }


# ── Utilities ──────────────────────────────────────────────────────────────────


_LIFECYCLE = (RunStart, RunEnd, TurnStart, TurnEnd)
_TERMINAL = (RunEnd, TurnEnd, ToolEnd, SubagentEnd, JobEnd, CompactionEnd)


def is_lifecycle_event(event: AgentEvent) -> bool:
    return isinstance(event, _LIFECYCLE)


def is_terminal_event(event: AgentEvent) -> bool:
    return isinstance(event, _TERMINAL)


def is_error_event(event: AgentEvent) -> bool:
    if isinstance(event, ErrorEvent):
        return True
    if isinstance(event, ToolEnd) and event.is_error:
        return True
    if isinstance(event, TurnEnd) and event.stop_reason == "error":
        return True
    return False
