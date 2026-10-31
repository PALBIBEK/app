"""Sessions: the manager, its errors, recovery and live fan-out.

Every name here is defined in exactly one module of this package.
"""

from __future__ import annotations

from supervisor.session.errors import (
    MemoryNotConfigured,
    OperationRetired,
    RecoveryBlocked,
    SessionAwaitingApproval,
    SessionError,
    SessionMoved,
    SessionNotFound,
    SessionTerminated,
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

__all__ = [
    "Dropped",
    "LiveHub",
    "LiveSubscription",
    "MemoryNotConfigured",
    "OperationRetired",
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
    "SessionMoved",
    "SessionNotFound",
    "SessionRegistry",
    "SessionTerminated",
    "ToolInvokeNotConfigured",
    "ToolInvokeOutcome",
    "WorkspaceUnavailable",
]
