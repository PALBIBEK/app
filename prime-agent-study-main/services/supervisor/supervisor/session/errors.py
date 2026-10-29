"""The errors a session raises: one hierarchy, defined once.

(Callers catch these by class; a second copy with the same names, as the
session package once had, never matches what the manager raises.)
"""

from __future__ import annotations

from supervisor.session.recovery import ResumeState

__all__ = [
    "MemoryNotConfigured",
    "RecoveryBlocked",
    "SessionAwaitingApproval",
    "SessionError",
    "SessionMoved",
    "SessionNotFound",
    "SessionTerminated",
    "SubagentNotConfigured",
    "ToolInvokeNotConfigured",
    "WorkspaceUnavailable",
]


class SessionError(Exception):
    pass


class SessionNotFound(SessionError):
    pass


class SessionTerminated(SessionError):
    pass


class SessionMoved(SessionError):
    """Another worker holds the session now: this one's sandbox no longer serves it."""

    retryable = False


class SessionAwaitingApproval(SessionError):
    pass


class ToolInvokeNotConfigured(SessionError):
    pass


class SubagentNotConfigured(SessionError):
    pass


class MemoryNotConfigured(SessionError):
    pass


class WorkspaceUnavailable(SessionError):
    """The session has no scope-bound workspace or no sandbox to put it in."""


class RecoveryBlocked(SessionError):
    def __init__(
        self,
        state: ResumeState,
        tool_call_ids: tuple[str, ...],
        *,
        session_id: str,
        compaction_ids: tuple[str, ...] = (),
    ) -> None:
        self.state = state
        self.tool_call_ids = tool_call_ids
        self.compaction_ids = compaction_ids
        self.session_id = session_id
        tools = ", ".join(tool_call_ids) or "(none)"
        compactions = ", ".join(compaction_ids) or "(none)"
        super().__init__(
            f"recovery blocked state={state} session={session_id} "
            f"tools={tools} compactions={compactions}"
        )
