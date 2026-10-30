"""Canonical event vocabulary. All runtimes emit these types."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


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
    # The call was closed before anything ran it (a stop, a lost step): this start is the
    # claim that keeps a late attempt from running it; its ToolEnd says it did not run.
    not_run: bool = False


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
    status: str  # responded | approved | denied | timed_out | cancelled
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
    """``messages``: the compacted conversation, the checkpoint later turns build on.

    ``replaced_from_seq``/``replaced_to_seq``: the journal entries its summary stands
    in for, which stay in the journal, readable verbatim.
    """

    compaction_id: str
    stop_reason: str  # ok | error | cancelled | deferred
    tokens_after: int | None = None
    summary_digest: str | None = None
    messages: list[dict[str, Any]] | None = None
    # The journal entries the summary replaced (inclusive): the model reopens them
    # with the ``history`` tool, so a compaction is never the only copy.
    replaced_from_seq: int | None = None
    replaced_to_seq: int | None = None
    # What the summary's model calls spent; it counts toward the run's and the session's
    # bounds like a model step's.
    usage: Usage | None = None
    # Inside a run (before a model step): ``messages`` is the whole conversation so far,
    # this run's messages included, and the run carries on from it.
    in_run: bool = False
    # Why it failed (``stop_reason="error"``): the conversation went on whole.
    error: str | None = None
    category: EventCategory = field(default=EventCategory.CONTEXT, repr=False)


@dataclass(frozen=True)
class MemoryRecall:
    """``text``: what was recalled for the run and shown to the model, when injected."""

    block_id: str
    source: str  # semantic | branch | custom
    token_estimate: int
    injected: bool
    relevance_score: float | None = None
    text: str | None = None
    category: EventCategory = field(default=EventCategory.CONTEXT, repr=False)


@dataclass(frozen=True)
class MemoryInjected:
    """Long-term memory shown to the model in one run (QM's per-turn recall record).

    ``text`` is what was injected (only new or changed facts after the first run);
    ``shown`` is every fact the model now holds ({notebook, id, hash, provenance}),
    which the next run's delta and capture provenance are computed from.
    """

    run_id: str
    text: str
    shown: list[dict[str, Any]] = field(default_factory=list)
    category: EventCategory = field(default=EventCategory.CONTEXT, repr=False)


@dataclass(frozen=True)
class MemoryContext:
    """Audience checkpoint (QM context boundary).

    When the audience a session speaks to changes, ``through_seq`` advances to the
    last event before the change: nothing at or before it re-enters a later turn.
    ``-1`` is a baseline (no cutoff).
    """

    audience: str
    through_seq: int
    category: EventCategory = field(default=EventCategory.CONTEXT, repr=False)


@dataclass(frozen=True)
class BackgroundResults:
    """Background subagents that finished since the model last heard about them.

    Recorded at the start of the next run and shown to the model ahead of that
    run's user message, so a background job's outcome reaches the agent without
    it having to ask. ``results``: ``{job_id, status, text}`` per finished job.
    """

    run_id: str
    results: list[dict[str, Any]] = field(default_factory=list)
    category: EventCategory = field(default=EventCategory.CONTEXT, repr=False)


# ── Hands (sandboxes) ──────────────────────────────────────────────────────────


@dataclass(frozen=True)
class HandProvisioned:
    """A sandbox now serves the session's tools.

    ``cause``: ``first_use`` (the first tool call that needed one), ``prewarm``
    (built as a run began, alongside the first model call), ``replaced`` (after a
    loss), ``resumed`` / ``prewarm_resumed`` (after an idle stop, restored from the
    last checkpoint), ``adopted`` (the one a dead worker left, still alive) or
    ``eager`` (a runtime that cannot start without one).
    """

    hand_id: str
    provider: str
    cause: str
    hand: str = "default"
    """Which of the session's hands (``default``, or a name its composition pins)."""
    category: EventCategory = field(default=EventCategory.META, repr=False)


@dataclass(frozen=True)
class HandCheckpointed:
    """What the session's sandbox held, saved while the model was thinking.

    Taken only for a step whose tools changed something; ``files`` and
    ``removed`` count what this checkpoint changed, ``processes`` is what was
    running (not restorable: a reset tells the model to restart them).
    """

    hand_id: str
    files: int
    removed: int
    processes: list[str] = field(default_factory=list)
    hand: str = "default"
    """Which of the session's hands (``default``, or a name its composition pins)."""
    category: EventCategory = field(default=EventCategory.META, repr=False)


@dataclass(frozen=True)
class HandStopped:
    """The session's sandbox was stopped on purpose (``reason``: ``idle``), its files saved
    first; the next tool call that needs one gets a fresh sandbox with them restored."""

    hand_id: str
    provider: str
    reason: str
    idle_s: float = 0.0
    hand: str = "default"
    category: EventCategory = field(default=EventCategory.META, repr=False)


@dataclass(frozen=True)
class HandLost:
    """A sandbox died under the session; the next tool call that needs one gets a fresh one."""

    hand_id: str
    provider: str
    reason: str
    hand: str = "default"
    """Which of the session's hands (``default``, or a name its composition pins)."""
    logs_ref: str | None = None
    """The provider's record of the hand (state, output tail), saved before it was destroyed:
    ``GET /v1/sessions/{id}/hand-logs/{logs_ref}``. ``None``: the provider keeps none."""
    logs_bytes: int = 0
    category: EventCategory = field(default=EventCategory.META, repr=False)


# ── Meta Events ────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Settled:
    category: EventCategory = field(default=EventCategory.META, repr=False)


@dataclass(frozen=True)
class ProjectContext:
    """The project's own instructions for the agent (``AGENTS.md``, ``CLAUDE.md``), read
    from the workspace as the run began; githi puts them in the system prompt, as pi does.
    """

    path: str
    text: str
    digest: str
    category: EventCategory = field(default=EventCategory.CONTEXT, repr=False)


@dataclass(frozen=True)
class WorkspaceRestored:
    """A hand's new sandbox was filled from its last checkpoint, and calls that changed
    files after that checkpoint ended before it was taken: their changes are gone.

    The journal still says they ran, so the model is told (after the round it is in)
    which calls to check and redo. ``lost``: "name (call id)" of each.
    """

    hand: str
    hand_id: str
    lost: list[str]
    text: str
    category: EventCategory = field(default=EventCategory.META, repr=False)


@dataclass(frozen=True)
class ModelRetry:
    """A model call failed in a way that may pass (rate limit, overload, a dropped
    connection); the run waits ``delay_s`` on a durable timer and asks again.

    pi's ``auto_retry_start``: shown to the user as "retrying (attempt of max_attempts)",
    so a slow provider reads as a wait, not a hang or an error.
    """

    step: int
    attempt: int
    max_attempts: int
    delay_s: float
    error_class: str
    detail: str
    category: EventCategory = field(default=EventCategory.META, repr=False)


@dataclass(frozen=True)
class ErrorEvent:
    error_class: str
    retryable: bool
    detail: str
    error_code: str | None = None
    category: EventCategory = field(default=EventCategory.META, repr=False)


# ── Union & Envelope ───────────────────────────────────────────────────────────


AgentEvent = (
    RunStart
    | RunEnd
    | TurnStart
    | TurnEnd
    | MessageEnd
    | ToolStart
    | ToolEnd
    | ApprovalRequired
    | ApprovalResolved
    | SubagentStart
    | SubagentEnd
    | JobStart
    | JobEnd
    | CompactionStart
    | CompactionEnd
    | MemoryRecall
    | MemoryInjected
    | MemoryContext
    | BackgroundResults
    | HandProvisioned
    | HandCheckpointed
    | HandLost
    | HandStopped
    | Settled
    | ModelRetry
    | ProjectContext
    | WorkspaceRestored
    | ErrorEvent
)


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


