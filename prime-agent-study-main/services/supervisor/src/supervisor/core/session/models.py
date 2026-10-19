"""
Session domain models.

Immutable, validated data classes representing session state.
All state changes create new instances (functional style).

Design Principles:
    1. Immutable: frozen=True, no setters
    2. Validated: __post_init__ checks invariants
    3. Typed: No Any types, strict typing
    4. Serializable: Easy JSON/DB conversion
    5. Self-documenting: Clear attribute names and docstrings

Usage:
    # Create session
    session = Session.create(
        tenant_id="tenant-1",
        config=SessionConfig(agent_id="agent-1"),
    )

    # Update state (returns new instance)
    session = session.with_state(SessionState.ACTIVE)

    # Record turn
    session = session.with_turn_completed(tokens_used=1500)
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from enum import Enum
from typing import Any


class SessionStatus(str, Enum):
    """Session lifecycle status."""

    CREATING = "creating"  # Being initialized
    ACTIVE = "active"  # Ready for messages
    PROCESSING = "processing"  # Turn in progress
    PAUSED = "paused"  # Manually paused
    AWAITING_APPROVAL = "awaiting_approval"  # HITL wait
    RECOVERING = "recovering"  # Cold start recovery
    TERMINATED = "terminated"  # Gracefully stopped
    FAILED = "failed"  # Unrecoverable error

    @property
    def is_terminal(self) -> bool:
        """Check if status is terminal (no recovery possible)."""
        return self in (SessionStatus.TERMINATED, SessionStatus.FAILED)

    @property
    def can_send_message(self) -> bool:
        """Check if session can receive messages."""
        return self in (SessionStatus.ACTIVE, SessionStatus.PAUSED)

    @property
    def is_busy(self) -> bool:
        """Check if session is busy processing."""
        return self in (
            SessionStatus.PROCESSING,
            SessionStatus.RECOVERING,
            SessionStatus.AWAITING_APPROVAL,
        )


class SessionMode(str, Enum):
    """Session execution mode."""

    STICKY = "sticky"  # Runtime stays alive between turns
    STATELESS = "stateless"  # Runtime respawns per turn

    @property
    def keeps_runtime_alive(self) -> bool:
        """Check if mode keeps runtime in memory."""
        return self == SessionMode.STICKY


@dataclass(frozen=True)
class SessionConfig:
    """
    Immutable session configuration.

    Set at creation time, cannot be modified.

    Attributes:
        agent_id: Agent definition to use.
        model_id: LLM model identifier.
        max_turns: Maximum turns before auto-terminate.
        max_tokens: Maximum total tokens budget.
        max_tool_iterations: Max tool calls per turn.
        turn_timeout_seconds: Timeout per turn.
        session_timeout_seconds: Total session timeout.
        tools_enabled: List of enabled tool names.
        system_prompt: Custom system prompt (optional).
        metadata: Arbitrary metadata.
    """

    agent_id: str
    model_id: str = "gpt-4"
    max_turns: int = 1000
    max_tokens: int = 1_000_000
    max_tool_iterations: int = 25
    turn_timeout_seconds: float = 300.0
    session_timeout_seconds: float = 86400.0  # 24 hours
    tools_enabled: tuple[str, ...] = ()
    system_prompt: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Validate configuration."""
        if not self.agent_id:
            raise ValueError("agent_id is required")
        if self.max_turns < 1:
            raise ValueError("max_turns must be >= 1")
        if self.max_tokens < 1:
            raise ValueError("max_tokens must be >= 1")
        if self.max_tool_iterations < 1:
            raise ValueError("max_tool_iterations must be >= 1")
        if self.turn_timeout_seconds <= 0:
            raise ValueError("turn_timeout_seconds must be > 0")


@dataclass(frozen=True)
class SessionMetrics:
    """
    Session usage metrics.

    Tracks resource consumption for billing and limits.

    Attributes:
        turn_count: Number of completed turns.
        message_count: Total messages (user + assistant).
        total_tokens: Total tokens consumed.
        tool_calls_count: Total tool invocations.
        total_duration_seconds: Total processing time.
        last_activity_at: Last activity timestamp.
    """

    turn_count: int = 0
    message_count: int = 0
    total_tokens: int = 0
    tool_calls_count: int = 0
    total_duration_seconds: float = 0.0
    last_activity_at: datetime | None = None

    def with_turn_completed(
        self,
        tokens_used: int = 0,
        tool_calls: int = 0,
        messages_added: int = 0,
        duration_seconds: float = 0.0,
    ) -> SessionMetrics:
        """Return new metrics with turn recorded."""
        return SessionMetrics(
            turn_count=self.turn_count + 1,
            message_count=self.message_count + messages_added,
            total_tokens=self.total_tokens + tokens_used,
            tool_calls_count=self.tool_calls_count + tool_calls,
            total_duration_seconds=self.total_duration_seconds + duration_seconds,
            last_activity_at=datetime.now(UTC),
        )

    def is_within_limits(self, config: SessionConfig) -> bool:
        """Check if metrics are within config limits."""
        return (
            self.turn_count < config.max_turns
            and self.total_tokens < config.max_tokens
        )


@dataclass(frozen=True)
class Session:
    """
    Immutable session entity.

    Represents a single agent session with all its state.
    State changes return new Session instances.

    Attributes:
        id: Unique session identifier.
        tenant_id: Owning tenant.
        config: Session configuration.
        status: Current lifecycle status.
        mode: Execution mode (sticky/stateless).
        metrics: Usage metrics.
        created_at: Creation timestamp.
        updated_at: Last update timestamp.
        trace_id: W3C trace ID for observability.
        scope_id: Workspace scope (optional).
        error: Error message if failed.
    """

    id: str
    tenant_id: str
    config: SessionConfig
    status: SessionStatus = SessionStatus.CREATING
    mode: SessionMode = SessionMode.STICKY
    metrics: SessionMetrics = field(default_factory=SessionMetrics)
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    trace_id: str | None = None
    scope_id: str | None = None
    error: str | None = None

    def __post_init__(self) -> None:
        """Validate session."""
        if not self.id:
            raise ValueError("id is required")
        if not self.tenant_id:
            raise ValueError("tenant_id is required")

    @classmethod
    def create(
        cls,
        tenant_id: str,
        config: SessionConfig,
        *,
        session_id: str | None = None,
        mode: SessionMode = SessionMode.STICKY,
        scope_id: str | None = None,
        trace_id: str | None = None,
    ) -> Session:
        """
        Create a new session.

        Args:
            tenant_id: Owning tenant.
            config: Session configuration.
            session_id: Custom ID (auto-generated if None).
            mode: Execution mode.
            scope_id: Workspace scope.
            trace_id: Trace ID for observability.

        Returns:
            New Session instance.
        """
        return cls(
            id=session_id or f"ses_{uuid.uuid4().hex[:16]}",
            tenant_id=tenant_id,
            config=config,
            status=SessionStatus.CREATING,
            mode=mode,
            scope_id=scope_id,
            trace_id=trace_id,
        )

    def with_status(self, status: SessionStatus, error: str | None = None) -> Session:
        """Return new session with updated status."""
        return replace(
            self,
            status=status,
            error=error,
            updated_at=datetime.now(UTC),
        )

    def with_turn_completed(
        self,
        tokens_used: int = 0,
        tool_calls: int = 0,
        messages_added: int = 0,
        duration_seconds: float = 0.0,
    ) -> Session:
        """Return new session with turn metrics recorded."""
        return replace(
            self,
            metrics=self.metrics.with_turn_completed(
                tokens_used=tokens_used,
                tool_calls=tool_calls,
                messages_added=messages_added,
                duration_seconds=duration_seconds,
            ),
            updated_at=datetime.now(UTC),
        )

    def with_trace_id(self, trace_id: str) -> Session:
        """Return new session with trace ID set."""
        return replace(self, trace_id=trace_id)

    @property
    def is_active(self) -> bool:
        """Check if session is active."""
        return self.status == SessionStatus.ACTIVE

    @property
    def is_terminal(self) -> bool:
        """Check if session is in terminal state."""
        return self.status.is_terminal

    @property
    def can_send_message(self) -> bool:
        """Check if session can receive messages."""
        return self.status.can_send_message

    @property
    def is_within_limits(self) -> bool:
        """Check if session is within resource limits."""
        return self.metrics.is_within_limits(self.config)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "id": self.id,
            "tenant_id": self.tenant_id,
            "status": self.status.value,
            "mode": self.mode.value,
            "config": {
                "agent_id": self.config.agent_id,
                "model_id": self.config.model_id,
                "max_turns": self.config.max_turns,
                "max_tokens": self.config.max_tokens,
                "tools_enabled": list(self.config.tools_enabled),
            },
            "metrics": {
                "turn_count": self.metrics.turn_count,
                "message_count": self.metrics.message_count,
                "total_tokens": self.metrics.total_tokens,
                "tool_calls_count": self.metrics.tool_calls_count,
            },
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "trace_id": self.trace_id,
            "scope_id": self.scope_id,
            "error": self.error,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Session:
        """Create from dictionary."""
        config = SessionConfig(
            agent_id=data["config"]["agent_id"],
            model_id=data["config"].get("model_id", "gpt-4"),
            max_turns=data["config"].get("max_turns", 1000),
            max_tokens=data["config"].get("max_tokens", 1_000_000),
            tools_enabled=tuple(data["config"].get("tools_enabled", [])),
        )

        metrics = SessionMetrics(
            turn_count=data.get("metrics", {}).get("turn_count", 0),
            message_count=data.get("metrics", {}).get("message_count", 0),
            total_tokens=data.get("metrics", {}).get("total_tokens", 0),
            tool_calls_count=data.get("metrics", {}).get("tool_calls_count", 0),
        )

        return cls(
            id=data["id"],
            tenant_id=data["tenant_id"],
            config=config,
            status=SessionStatus(data["status"]),
            mode=SessionMode(data.get("mode", "sticky")),
            metrics=metrics,
            created_at=datetime.fromisoformat(data["created_at"]),
            updated_at=datetime.fromisoformat(data["updated_at"]),
            trace_id=data.get("trace_id"),
            scope_id=data.get("scope_id"),
            error=data.get("error"),
        )


@dataclass(frozen=True)
class Turn:
    """
    Represents a single conversation turn.

    A turn is one user message → agent response cycle.

    Attributes:
        id: Unique turn identifier.
        session_id: Parent session.
        turn_number: Sequential turn number.
        user_message: User's input.
        assistant_message: Agent's response (None if incomplete).
        tool_calls: Tool invocations in this turn.
        tokens_used: Tokens consumed.
        status: Turn completion status.
        started_at: Turn start time.
        completed_at: Turn completion time.
        error: Error if failed.
    """

    id: str
    session_id: str
    turn_number: int
    user_message: str
    assistant_message: str | None = None
    tool_calls: tuple[dict[str, Any], ...] = ()
    tokens_used: int = 0
    status: str = "processing"
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    completed_at: datetime | None = None
    error: str | None = None

    @classmethod
    def create(
        cls,
        session_id: str,
        turn_number: int,
        user_message: str,
    ) -> Turn:
        """Create a new turn."""
        return cls(
            id=f"{session_id}-turn-{turn_number}",
            session_id=session_id,
            turn_number=turn_number,
            user_message=user_message,
        )

    def with_completion(
        self,
        assistant_message: str,
        tool_calls: list[dict[str, Any]] | None = None,
        tokens_used: int = 0,
    ) -> Turn:
        """Return completed turn."""
        return replace(
            self,
            assistant_message=assistant_message,
            tool_calls=tuple(tool_calls or []),
            tokens_used=tokens_used,
            status="completed",
            completed_at=datetime.now(UTC),
        )

    def with_error(self, error: str) -> Turn:
        """Return failed turn."""
        return replace(
            self,
            status="failed",
            error=error,
            completed_at=datetime.now(UTC),
        )

    @property
    def duration_seconds(self) -> float:
        """Calculate turn duration."""
        if self.completed_at:
            return (self.completed_at - self.started_at).total_seconds()
        return (datetime.now(UTC) - self.started_at).total_seconds()


@dataclass(frozen=True)
class Message:
    """
    A single message in conversation history.

    Attributes:
        role: Message role (user, assistant, system, tool).
        content: Message content.
        tool_calls: Tool calls (for assistant messages).
        tool_call_id: Tool call ID (for tool messages).
        name: Tool name (for tool messages).
    """

    role: str
    content: str
    tool_calls: tuple[dict[str, Any], ...] = ()
    tool_call_id: str | None = None
    name: str | None = None

    def __post_init__(self) -> None:
        """Validate message."""
        valid_roles = ("user", "assistant", "system", "tool")
        if self.role not in valid_roles:
            raise ValueError(f"role must be one of {valid_roles}")

    def to_dict(self) -> dict[str, Any]:
        """Convert to LLM API format."""
        d: dict[str, Any] = {"role": self.role, "content": self.content}

        if self.tool_calls:
            d["tool_calls"] = list(self.tool_calls)
        if self.tool_call_id:
            d["tool_call_id"] = self.tool_call_id
        if self.name:
            d["name"] = self.name

        return d

    @classmethod
    def user(cls, content: str) -> Message:
        """Create user message."""
        return cls(role="user", content=content)

    @classmethod
    def assistant(
        cls,
        content: str,
        tool_calls: list[dict[str, Any]] | None = None,
    ) -> Message:
        """Create assistant message."""
        return cls(
            role="assistant",
            content=content,
            tool_calls=tuple(tool_calls or []),
        )

    @classmethod
    def system(cls, content: str) -> Message:
        """Create system message."""
        return cls(role="system", content=content)

    @classmethod
    def tool(cls, tool_call_id: str, name: str, content: str) -> Message:
        """Create tool result message."""
        return cls(
            role="tool",
            content=content,
            tool_call_id=tool_call_id,
            name=name,
        )
