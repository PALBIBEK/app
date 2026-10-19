"""Core domain types and interfaces for session management.

This module provides the foundational types used throughout the session layer:
- Enums for session states and modes
- Frozen dataclasses for immutable data structures
- Exception hierarchy with structured error codes
- Protocol interfaces for dependency injection
"""

from __future__ import annotations

from supervisor.session.core.enums import (
    ChildActivity,
    JobActivity,
    ResumeState,
    SessionMode,
    SessionStatus,
    mode_for_harness,
    mode_for_manifest,
)
from supervisor.session.core.types import (
    BeginOutcome,
    ChildRow,
    JobRow,
    RegistryMetrics,
    ResumeDecision,
    ResumeResult,
    SessionInfo,
    SessionMetrics,
    SessionTree,
    ToolInvokeOutcome,
)
from supervisor.session.core.errors import (
    MemoryNotConfigured,
    RecoveryBlocked,
    SessionAwaitingApproval,
    SessionError,
    SessionNotFound,
    SessionTerminated,
    SubagentNotConfigured,
    ToolInvokeNotConfigured,
    _auto_context,
)
from supervisor.session.core.protocols import (
    RecoveryProtocol,
    SessionManagerProtocol,
    SessionRegistryProtocol,
    SessionRowView,
)

__all__ = [
    # Enums
    "ChildActivity",
    "JobActivity",
    "ResumeState",
    "SessionMode",
    "SessionStatus",
    "mode_for_harness",
    "mode_for_manifest",
    # Types
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
    # Errors
    "MemoryNotConfigured",
    "RecoveryBlocked",
    "SessionAwaitingApproval",
    "SessionError",
    "SessionNotFound",
    "SessionTerminated",
    "SubagentNotConfigured",
    "ToolInvokeNotConfigured",
    "_auto_context",
    # Protocols
    "RecoveryProtocol",
    "SessionManagerProtocol",
    "SessionRegistryProtocol",
    "SessionRowView",
]
