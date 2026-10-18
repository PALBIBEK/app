"""
Githi agent state management.

Immutable state tracking for agent sessions following PYTHON_PATTERNS_MASTERCLASS patterns:
- Frozen dataclasses for immutability
- Type hints throughout
- Factory methods for creation
- DTO pattern for serialization
- Event listener support (from githi/src/githi/agent/state.py)

Combines the immutable DTO pattern with mutable runtime state for the agent loop.
"""

from __future__ import annotations

import typing as t
from collections import Counter
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum, auto

if t.TYPE_CHECKING:
    from supervisor.agent.base import AgentState as AgentStateProtocol
    from supervisor.agent.runtimes.githi.events import Event


class StopReason(Enum):
    """Reasons why the agent run stopped."""
    DONE = auto()
    MAX_TURNS = auto()
    ERROR = auto()
    STOPPED = auto()
    INTERRUPTED = auto()


class EventType(Enum):
    """Event types emitted by the agent."""
    TURN_START = "turn_start"
    TURN_END = "turn_end"
    TOOL_START = "tool_start"
    TOOL_END = "tool_end"
    TOOL_PROGRESS = "tool_progress"
    TEXT_DELTA = "text_delta"
    THINKING_START = "thinking_start"
    THINKING_DELTA = "thinking_delta"
    THINKING_END = "thinking_end"
    AGENT_END = "agent_end"
    MEMORY_RECALL = "memory_recall"


@dataclass
class RuntimeEvent:
    """Event emitted during agent execution."""
    type: EventType
    data: dict[str, t.Any] = field(default_factory=dict)


@dataclass
class MutableAgentState:
    """Mutable run state with listener registry.

    Used during active runs for efficient state updates.
    Ported from githi/src/githi/agent/state.py.
    """
    messages: list[dict] = field(default_factory=list)
    turns: int = 0
    tool_calls: int = 0
    tool_counts: Counter = field(default_factory=Counter)
    """Per-tool call counts. Kept alongside `turns` because the gap between
    them is the evidence that the model is batching."""
    stop_reason: StopReason | None = None
    _listeners: list[t.Callable[[RuntimeEvent], None]] = field(default_factory=list)

    def append(self, message: dict) -> None:
        """Append a message to the transcript."""
        self.messages.append(message)

    def record_turn(self) -> None:
        """Record a completed turn."""
        self.turns += 1

    def record_tool_call(self, name: str) -> None:
        """Record a tool call."""
        self.tool_calls += 1
        self.tool_counts[name] += 1

    def last_assistant_text(self) -> str:
        """Get the most recent assistant message text.

        Messages that only request tools are skipped: they are steps in the
        work, not the answer.
        """
        for message in reversed(self.messages):
            if message.get("role") == "assistant":
                content = message.get("content", "")
                if isinstance(content, str) and content.strip():
                    return content
                elif isinstance(content, list):
                    text_parts = [
                        p.get("text", "") for p in content
                        if isinstance(p, dict) and p.get("type") == "text"
                    ]
                    text = "".join(text_parts)
                    if text.strip():
                        return text
        return ""

    # --- listeners ---

    def on(self, listener: t.Callable[[RuntimeEvent], None]) -> None:
        """Register an event listener."""
        self._listeners.append(listener)

    def off(self, listener: t.Callable[[RuntimeEvent], None]) -> None:
        """Remove a listener if present."""
        if listener in self._listeners:
            self._listeners.remove(listener)

    def emit(self, event: RuntimeEvent) -> None:
        """Emit an event to all listeners."""
        for listener in self._listeners:
            try:
                listener(event)
            except Exception:  # noqa: BLE001 - observability must not fail a run
                continue

    def emit_event(self, event_type: EventType, data: dict[str, t.Any] | None = None) -> None:
        """Convenience method to emit an event by type."""
        self.emit(RuntimeEvent(type=event_type, data=data or {}))


@dataclass(frozen=True)
class Usage:
    """
    Token usage and cost tracking.

    Immutable value object for LLM usage metrics. Separates cache tokens
    for accurate cost calculation (cache reads ~10x cheaper than input tokens).

    Pattern: Immutable DTO (PYTHON_PATTERNS_MASTERCLASS.md Section 7.1)
    """

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        """Total token count across all categories"""
        return (
            self.input_tokens
            + self.output_tokens
            + self.cache_read_tokens
            + self.cache_write_tokens
        )

    def __add__(self, other: Usage) -> Usage:
        """
        Add two usage objects together.

        Enables cumulative usage tracking:
            total_usage = usage1 + usage2 + usage3
        """
        if not isinstance(other, Usage):
            return NotImplemented

        return Usage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            cache_read_tokens=self.cache_read_tokens + other.cache_read_tokens,
            cache_write_tokens=self.cache_write_tokens + other.cache_write_tokens,
        )

    def to_dict(self) -> dict[str, int]:
        """Serialize to dictionary"""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, int]) -> Usage:
        """Deserialize from dictionary"""
        return cls(**{k: v for k, v in data.items() if k in {
            "input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens"
        }})

    def summary(self, model: str) -> str:
        """Format usage summary for display"""
        parts = [
            f"in={self.input_tokens:,}",
            f"out={self.output_tokens:,}",
        ]
        if self.cache_read_tokens > 0:
            parts.append(f"cache_read={self.cache_read_tokens:,}")
        if self.cache_write_tokens > 0:
            parts.append(f"cache_write={self.cache_write_tokens:,}")

        total = self.total_tokens
        return f"{model} | {' '.join(parts)} | total={total:,}"


@dataclass(frozen=True)
class GithiAgentState:
    """
    Current state of githi agent session.

    Immutable snapshot of agent state at a point in time. Create new instances
    for state transitions using evolve() or replace().

    Implements: AgentState protocol (contracts/AGENT_CONTRACT.md)
    Pattern: Immutable state object (PYTHON_PATTERNS_MASTERCLASS.md)

    Example:
        # Initial state
        state = GithiAgentState(
            session_id="sess-123",
            model="anthropic/claude-sonnet-4",
            context_window=200000
        )

        # After turn completes (create new instance)
        new_state = state.with_turn_complete(usage=usage_obj)
    """

    # Identity
    session_id: str
    model: str
    context_window: int

    # Flags (observable by supervisor)
    is_streaming: bool = False
    is_compacting: bool = False

    # Counters
    message_count: int = 0
    pending_count: int = 0  # Queued messages
    turn_count: int = 0
    tool_call_count: int = 0
    compaction_count: int = 0

    # Usage tracking (cumulative)
    total_usage: Usage = field(default_factory=Usage)
    total_cost_usd: Decimal = field(default_factory=lambda: Decimal("0.00"))

    # Timestamps
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    last_active_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    # Stop reason (if stopped)
    stop_reason: str | None = None  # "done" | "max_turns" | "error" | "stopped" | "interrupted"

    def with_turn_start(self) -> GithiAgentState:
        """
        Create new state for turn start.

        Returns:
            New state instance with is_streaming=True
        """
        return GithiAgentState(
            session_id=self.session_id,
            model=self.model,
            context_window=self.context_window,
            is_streaming=True,  # Changed
            is_compacting=self.is_compacting,
            message_count=self.message_count,
            pending_count=self.pending_count,
            turn_count=self.turn_count + 1,  # Incremented
            tool_call_count=self.tool_call_count,
            compaction_count=self.compaction_count,
            total_usage=self.total_usage,
            total_cost_usd=self.total_cost_usd,
            started_at=self.started_at,
            last_active_at=datetime.now(timezone.utc),  # Updated
            stop_reason=self.stop_reason,
        )

    def with_turn_complete(self, usage: Usage, cost_usd: Decimal, tool_calls: int = 0) -> GithiAgentState:
        """
        Create new state for turn completion.

        Args:
            usage: Usage for this turn
            cost_usd: Cost for this turn
            tool_calls: Number of tool calls this turn

        Returns:
            New state instance with updated usage
        """
        return GithiAgentState(
            session_id=self.session_id,
            model=self.model,
            context_window=self.context_window,
            is_streaming=False,  # Changed
            is_compacting=self.is_compacting,
            message_count=self.message_count + 1,  # Incremented (new message)
            pending_count=self.pending_count,
            turn_count=self.turn_count,
            tool_call_count=self.tool_call_count + tool_calls,  # Added
            compaction_count=self.compaction_count,
            total_usage=self.total_usage + usage,  # Accumulated
            total_cost_usd=self.total_cost_usd + cost_usd,  # Accumulated
            started_at=self.started_at,
            last_active_at=datetime.now(timezone.utc),  # Updated
            stop_reason=self.stop_reason,
        )

    def with_compaction_start(self) -> GithiAgentState:
        """Create new state for compaction start"""
        return GithiAgentState(
            session_id=self.session_id,
            model=self.model,
            context_window=self.context_window,
            is_streaming=self.is_streaming,
            is_compacting=True,  # Changed
            message_count=self.message_count,
            pending_count=self.pending_count,
            turn_count=self.turn_count,
            tool_call_count=self.tool_call_count,
            compaction_count=self.compaction_count,
            total_usage=self.total_usage,
            total_cost_usd=self.total_cost_usd,
            started_at=self.started_at,
            last_active_at=datetime.now(timezone.utc),
            stop_reason=self.stop_reason,
        )

    def with_compaction_complete(self, usage: Usage, cost_usd: Decimal) -> GithiAgentState:
        """Create new state for compaction completion"""
        return GithiAgentState(
            session_id=self.session_id,
            model=self.model,
            context_window=self.context_window,
            is_streaming=self.is_streaming,
            is_compacting=False,  # Changed
            message_count=self.message_count,
            pending_count=self.pending_count,
            turn_count=self.turn_count,
            tool_call_count=self.tool_call_count,
            compaction_count=self.compaction_count + 1,  # Incremented
            total_usage=self.total_usage + usage,  # Accumulated
            total_cost_usd=self.total_cost_usd + cost_usd,  # Accumulated
            started_at=self.started_at,
            last_active_at=datetime.now(timezone.utc),
            stop_reason=self.stop_reason,
        )

    def with_stop(self, reason: str) -> GithiAgentState:
        """
        Create new state for session stop.

        Args:
            reason: "done" | "max_turns" | "error" | "stopped" | "interrupted"
        """
        return GithiAgentState(
            session_id=self.session_id,
            model=self.model,
            context_window=self.context_window,
            is_streaming=False,  # Force stop streaming
            is_compacting=False,  # Force stop compacting
            message_count=self.message_count,
            pending_count=self.pending_count,
            turn_count=self.turn_count,
            tool_call_count=self.tool_call_count,
            compaction_count=self.compaction_count,
            total_usage=self.total_usage,
            total_cost_usd=self.total_cost_usd,
            started_at=self.started_at,
            last_active_at=datetime.now(timezone.utc),
            stop_reason=reason,  # Set
        )

    def with_pending_messages(self, count: int) -> GithiAgentState:
        """Update pending message count"""
        return GithiAgentState(
            session_id=self.session_id,
            model=self.model,
            context_window=self.context_window,
            is_streaming=self.is_streaming,
            is_compacting=self.is_compacting,
            message_count=self.message_count,
            pending_count=count,  # Updated
            turn_count=self.turn_count,
            tool_call_count=self.tool_call_count,
            compaction_count=self.compaction_count,
            total_usage=self.total_usage,
            total_cost_usd=self.total_cost_usd,
            started_at=self.started_at,
            last_active_at=datetime.now(timezone.utc),
            stop_reason=self.stop_reason,
        )

    @property
    def is_settled(self) -> bool:
        """
        Check if session is settled (safe to checkpoint).

        Per contracts/AGENT_CONTRACT.md: settled = not streaming AND not compacting
        AND no pending work.
        """
        return (
            not self.is_streaming
            and not self.is_compacting
            and self.pending_count == 0
        )

    def to_dict(self) -> dict[str, t.Any]:
        """
        Serialize to dictionary.

        Returns:
            Dict suitable for JSON serialization
        """
        return {
            "session_id": self.session_id,
            "model": self.model,
            "context_window": self.context_window,
            "is_streaming": self.is_streaming,
            "is_compacting": self.is_compacting,
            "message_count": self.message_count,
            "pending_count": self.pending_count,
            "turn_count": self.turn_count,
            "tool_call_count": self.tool_call_count,
            "compaction_count": self.compaction_count,
            "total_usage": self.total_usage.to_dict(),
            "total_cost_usd": str(self.total_cost_usd),
            "started_at": self.started_at.isoformat(),
            "last_active_at": self.last_active_at.isoformat(),
            "stop_reason": self.stop_reason,
        }

    @classmethod
    def from_dict(cls, data: dict[str, t.Any]) -> GithiAgentState:
        """
        Deserialize from dictionary.

        Args:
            data: Dictionary from to_dict()

        Returns:
            GithiAgentState instance
        """
        return cls(
            session_id=data["session_id"],
            model=data["model"],
            context_window=data["context_window"],
            is_streaming=data.get("is_streaming", False),
            is_compacting=data.get("is_compacting", False),
            message_count=data.get("message_count", 0),
            pending_count=data.get("pending_count", 0),
            turn_count=data.get("turn_count", 0),
            tool_call_count=data.get("tool_call_count", 0),
            compaction_count=data.get("compaction_count", 0),
            total_usage=Usage.from_dict(data.get("total_usage", {})),
            total_cost_usd=Decimal(data.get("total_cost_usd", "0.00")),
            started_at=datetime.fromisoformat(data["started_at"]),
            last_active_at=datetime.fromisoformat(data["last_active_at"]),
            stop_reason=data.get("stop_reason"),
        )

    def __repr__(self) -> str:
        """Readable representation for debugging"""
        return (
            f"GithiAgentState(session={self.session_id!r}, "
            f"turns={self.turn_count}, tools={self.tool_call_count}, "
            f"tokens={self.total_usage.total_tokens:,}, "
            f"cost=${self.total_cost_usd}, "
            f"settled={self.is_settled})"
        )
