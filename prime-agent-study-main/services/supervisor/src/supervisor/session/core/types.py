"""Session domain types - frozen dataclasses with slots.

All types are immutable after creation for thread-safe sharing
and prevention of mutation bugs.
"""

from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any, Literal

from supervisor.session.core.enums import (
    ChildActivity,
    JobActivity,
    ResumeState,
    SessionMode,
    SessionStatus,
)

if TYPE_CHECKING:
    from supervisor.harness import HarnessManifest, RecoveryAction
    from supervisor.seams import ToolResult

__all__ = [
    "BeginOutcome",
    "ChildRow",
    "JobRow",
    "LiveSubscription",
    "RegistryMetrics",
    "ResumeDecision",
    "ResumeResult",
    "SessionInfo",
    "SessionMetrics",
    "SessionTree",
    "ToolInvokeOutcome",
]


# =============================================================================
# Session Info Types
# =============================================================================


@dataclass(frozen=True, slots=True)
class SessionInfo:
    """Complete session metadata for API responses.

    Immutable snapshot of session state for external consumption.
    """

    session_id: str
    tenant_id: str
    harness_version: str
    runtime: str
    status: SessionStatus
    mode: SessionMode
    is_busy: bool
    created_at: datetime | None = None
    composition_id: str | None = None
    sandbox_id: str | None = None
    parent_session_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "session_id": self.session_id,
            "tenant_id": self.tenant_id,
            "harness_version": self.harness_version,
            "runtime": self.runtime,
            "status": self.status.value,
            "mode": self.mode.value,
            "is_busy": self.is_busy,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "composition_id": self.composition_id,
            "sandbox_id": self.sandbox_id,
            "parent_session_id": self.parent_session_id,
        }


# =============================================================================
# Recovery Types
# =============================================================================


@dataclass(frozen=True, slots=True)
class ResumeDecision:
    """Result of recovery decision engine.

    Determines how a session should be resumed after restart.
    """

    state: ResumeState
    harness: HarnessManifest | None = None
    unpaired_tool_call_ids: tuple[str, ...] = ()
    unpaired_compaction_ids: tuple[str, ...] = ()
    applied_actions: tuple[RecoveryAction, ...] = ()


@dataclass(frozen=True, slots=True)
class ResumeResult:
    """Result of session resume operation.

    Returned from SessionManager.resume() to indicate outcome.
    """

    state: ResumeState
    session_id: str
    unpaired_tool_call_ids: tuple[str, ...] = ()
    unpaired_compaction_ids: tuple[str, ...] = ()


# =============================================================================
# Child Session / Tree Types
# =============================================================================


@dataclass(frozen=True, slots=True)
class ChildRow:
    """Represents a child session in the session tree.

    Immutable record of a spawned subagent or forked session.
    """

    id: str
    mode: str
    provider: str
    parent_id: str
    run_id: str
    label: str
    activity: ChildActivity
    depth: int


@dataclass(frozen=True, slots=True)
class JobRow:
    """Represents a background job execution.

    Immutable record of a job spawned within a session.
    """

    job_id: str
    kind: str
    activity: JobActivity
    stop_reason: str | None = None
    output: str | None = None


@dataclass
class SessionTree:
    """In-memory tree of child sessions and jobs.

    Mutable structure rebuilt from journal events.
    """

    children: dict[str, ChildRow] = field(default_factory=dict)
    jobs: dict[str, JobRow] = field(default_factory=dict)
    unpaired_run_ids: set[str] = field(default_factory=set)
    open_job_ids: set[str] = field(default_factory=set)

    def list_children(self, scope: Literal["direct", "descendants"] = "direct") -> list[ChildRow]:
        """Return child sessions.

        Args:
            scope: "direct" for immediate children, "descendants" for all

        Returns:
            List of ChildRow instances
        """
        return list(self.children.values())

    def list_jobs(self) -> list[JobRow]:
        """Return all jobs."""
        return list(self.jobs.values())


# =============================================================================
# Tool Invocation Types
# =============================================================================


@dataclass(frozen=True, slots=True)
class ToolInvokeOutcome:
    """Result of tool invocation.

    Returned from SessionManager.invoke_tool().
    """

    tool_call_id: str
    result: ToolResult


@dataclass(frozen=True, slots=True)
class BeginOutcome:
    """Outcome of ToolOutbox.begin() operation.

    Indicates whether to execute or return cached result.
    """

    action: Literal["execute", "cached"]
    tool_call_id: str
    cached: ToolResult | None = None


# =============================================================================
# Live Event Types
# =============================================================================


@dataclass
class LiveSubscription:
    """Per-subscriber event queue for SSE streaming.

    Mutable to track dropped state.
    """

    queue: asyncio.Queue = field(default_factory=lambda: asyncio.Queue(maxsize=256))
    dropped: bool = False

    def __hash__(self) -> int:
        """Use object identity for hashing."""
        return id(self)

    def __eq__(self, other: object) -> bool:
        """Use object identity for equality."""
        return self is other


# =============================================================================
# Metrics Types
# =============================================================================


@dataclass
class RegistryMetrics:
    """Metrics for session registry operations.

    Tracks registration, lock contention, and other operational stats.
    """

    registrations: int = 0
    unregistrations: int = 0
    lock_acquisitions: int = 0
    lock_contentions: int = 0
    active_sessions: int = 0

    def to_dict(self) -> dict[str, int]:
        """Convert to dictionary for export."""
        return asdict(self)


@dataclass
class SessionMetrics:
    """Per-session operation metrics.

    Tracks usage and costs for a single session.
    """

    messages_sent: int = 0
    tools_invoked: int = 0
    subagents_spawned: int = 0
    compactions_run: int = 0
    total_cost: float = 0.0
    total_input_tokens: int = 0
    total_output_tokens: int = 0

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for export."""
        return asdict(self)
