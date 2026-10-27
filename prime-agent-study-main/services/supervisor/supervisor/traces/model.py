"""A run's trace: a flat list of spans (each naming its parent) and the run's rollup.

Spans travel flat, ordered by start; the reader builds the tree from ``parent_id``.
``detail`` holds a span's full content (messages, arguments, results); ``preview``
is the one line a list shows. ``to_dict(full=False)`` drops ``detail`` so a tree
stays small; one span's detail is asked for separately.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any

from supervisor.traces.kinds import SpanKind

__all__ = ["Marker", "Rollup", "RunStatus", "RunTrace", "Span", "SpanStatus", "TimeSplit"]


class SpanStatus(StrEnum):
    OK = "ok"
    ERROR = "error"
    RUNNING = "running"
    """Still open: the run is going."""
    UNFINISHED = "unfinished"
    """Never closed: the run ended (or was lost) while it was open."""


class RunStatus(StrEnum):
    RUNNING = "running"
    AWAITING_APPROVAL = "awaiting_approval"
    OK = "ok"
    ERROR = "error"
    CANCELLED = "cancelled"
    BUDGET_EXCEEDED = "budget_exceeded"
    INTERRUPTED = "interrupted"
    """No end in the journal and the session is no longer running it."""

    @property
    def finished(self) -> bool:
        return self not in (RunStatus.RUNNING, RunStatus.AWAITING_APPROVAL)


@dataclass
class Marker:
    """Something that happened at a moment inside a span (a sandbox starting, a checkpoint)."""

    ts_ms: int
    name: str
    text: str
    seq: int


@dataclass
class Span:
    id: str
    parent_id: str | None
    kind: SpanKind
    name: str
    start_ms: int
    end_ms: int | None = None
    status: SpanStatus = SpanStatus.RUNNING
    error: dict[str, str] | None = None
    error_below: bool = False
    """A span under this one failed (lets a collapsed tree still show where it went wrong)."""
    attrs: dict[str, Any] = field(default_factory=dict)
    """Small typed facts: model, tokens, cost, tool name, decision, exit code."""
    preview: str | None = None
    detail: dict[str, Any] = field(default_factory=dict)
    markers: list[Marker] = field(default_factory=list)
    seqs: list[int] = field(default_factory=list)
    """The journal events this span was built from."""

    @property
    def duration_ms(self) -> int | None:
        return None if self.end_ms is None else max(0, self.end_ms - self.start_ms)

    def to_dict(self, *, full: bool = False) -> dict[str, Any]:
        out = asdict(self)
        out["duration_ms"] = self.duration_ms
        if not full:
            out.pop("detail")
        return out


@dataclass
class TimeSplit:
    """Where a run's wall time went. Overlaps count once, in the first bucket that claims them
    (human, then tool, then model); ``other_ms`` is what no span covers."""

    model_ms: int = 0
    tool_ms: int = 0
    human_ms: int = 0
    other_ms: int = 0


@dataclass
class Rollup:
    status: RunStatus
    reason: str | None
    started_ms: int
    ended_ms: int | None
    duration_ms: int | None
    time: TimeSplit
    input: str | None
    """The user's message, shortened."""
    output: str | None
    """The run's last answer, shortened."""
    cost: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    steps: int = 0
    llm_calls: int = 0
    tool_calls: int = 0
    tool_errors: int = 0
    approvals: int = 0
    denials: int = 0
    """Approvals that were denied or ran out of time."""
    errors: int = 0
    models: list[str] = field(default_factory=list)
    tools: dict[str, int] = field(default_factory=dict)
    slowest_span_id: str | None = None
    costliest_span_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class RunTrace:
    session_id: str
    run_id: str
    trace_id: str
    spans: list[Span]
    rollup: Rollup

    def span(self, span_id: str) -> Span | None:
        return next((s for s in self.spans if s.id == span_id), None)

    def to_dict(self, *, full: bool = False) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "run_id": self.run_id,
            "trace_id": self.trace_id,
            "rollup": self.rollup.to_dict(),
            "spans": [s.to_dict(full=full) for s in self.spans],
        }
