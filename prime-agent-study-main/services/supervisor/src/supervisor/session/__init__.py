"""Sessions: the manager, its errors, recovery, live fan-out and the child tree.

Every name here is defined in exactly one module of this package.
"""

from __future__ import annotations

from supervisor.session.errors import (
    MemoryNotConfigured,
    RecoveryBlocked,
    SessionAwaitingApproval,
    SessionError,
    SessionNotFound,
    SessionTerminated,
    SubagentNotConfigured,
    ToolInvokeNotConfigured,
    WorkspaceUnavailable,
)
from supervisor.session.live import Dropped, LiveHub, LiveSubscription
from supervisor.session.manager import (
    ResumeResult,
    SessionInfo,
    SessionManager,
    ToolInvokeOutcome,
)
from supervisor.session.modes import SessionMode
from supervisor.session.recovery import Recovery, ResumeDecision, ResumeState
from supervisor.session.registry import SessionRegistry
from supervisor.session.tree import (
    SessionTree,
    build_session_tree,
    unpaired_compaction_starts,
    unpaired_subagent_starts,
)

__all__ = [
    "Dropped",
    "LiveHub",
    "LiveSubscription",
    "MemoryNotConfigured",
    "Recovery",
    "RecoveryBlocked",
    "ResumeDecision",
    "ResumeResult",
    "ResumeState",
    "SessionAwaitingApproval",
    "SessionError",
    "SessionInfo",
    "SessionManager",
    "SessionMode",
    "SessionNotFound",
    "SessionRegistry",
    "SessionTerminated",
    "SessionTree",
    "SubagentNotConfigured",
    "ToolInvokeNotConfigured",
    "ToolInvokeOutcome",
    "WorkspaceUnavailable",
    "build_session_tree",
    "unpaired_compaction_starts",
    "unpaired_subagent_starts",
]
