"""Session exception hierarchy with structured error codes.

All exceptions include:
- error_code: Machine-readable code for API responses
- http_status: Appropriate HTTP status code
- retryable: Whether the operation may succeed on retry
- detail: Structured context dictionary for debugging

The @_auto_context decorator auto-populates the detail dict
from dataclass fields for DRY error definitions.
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from supervisor.session.core.enums import ResumeState

__all__ = [
    "MemoryNotConfigured",
    "RecoveryBlocked",
    "SessionAwaitingApproval",
    "SessionError",
    "SessionNotFound",
    "SessionTerminated",
    "SubagentNotConfigured",
    "ToolInvokeNotConfigured",
    "_auto_context",
]


# =============================================================================
# Auto-Context Decorator
# =============================================================================

_BASE_FIELDS = frozenset({
    "message",
    "detail",
    "error_code",
    "retryable",
    "http_status",
    "args",
})


def _auto_context(cls: type) -> type:
    """Decorator: auto-populate detail dict from dataclass fields.

    This decorator wraps __init__ to automatically add all non-base
    fields to the detail dictionary, eliminating repetitive code
    in error class definitions.

    Example:
        @_auto_context
        @dataclass
        class MyError(SessionError):
            session_id: str = ""
            tool_name: str = ""

        # detail will automatically contain {"session_id": ..., "tool_name": ...}
    """
    orig_post_init = getattr(cls, "__post_init__", None)

    def __post_init__(self: Any) -> None:
        # Run original post_init if exists
        if orig_post_init is not None:
            orig_post_init(self)

        # Auto-populate detail from non-base fields
        for f in fields(self):
            if f.name not in _BASE_FIELDS:
                val = getattr(self, f.name, None)
                if val is not None:
                    self.detail[f.name] = val

    cls.__post_init__ = __post_init__
    return cls


# =============================================================================
# Base Exception
# =============================================================================


@dataclass
class SessionError(Exception):
    """Base session exception with structured metadata.

    All session-layer exceptions inherit from this class.

    Attributes:
        message: Human-readable error description
        error_code: Machine-readable code for API responses
        http_status: Appropriate HTTP status code
        retryable: Whether the operation may succeed on retry
        detail: Structured context for debugging
    """

    message: str = ""
    error_code: str = "SESSION_ERROR"
    http_status: int = 500
    retryable: bool = False
    detail: dict[str, Any] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        """Initialize exception with message and detail dict."""
        if self.detail is None:
            self.detail = {}
        super().__init__(self.message)

    def __str__(self) -> str:
        """Return the error message."""
        return self.message

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for API responses.

        Returns:
            Dictionary with error_code, message, retryable, and detail.
        """
        return {
            "error_code": self.error_code,
            "message": self.message,
            "retryable": self.retryable,
            "detail": self.detail,
        }


# =============================================================================
# Session Lifecycle Errors
# =============================================================================


@_auto_context
@dataclass
class SessionNotFound(SessionError):
    """Requested session does not exist.

    Raised when attempting to operate on a non-existent session.
    """

    error_code: str = "SESSION_NOT_FOUND"
    http_status: int = 404
    session_id: str = ""

    def __post_init__(self) -> None:
        if not self.message:
            self.message = f"session not found: {self.session_id}"
        super().__post_init__()


@_auto_context
@dataclass
class SessionTerminated(SessionError):
    """Session has been terminated and cannot accept operations.

    Raised when attempting to use a terminated session.
    """

    error_code: str = "SESSION_TERMINATED"
    http_status: int = 410
    session_id: str = ""

    def __post_init__(self) -> None:
        if not self.message:
            self.message = f"session terminated: {self.session_id}"
        super().__post_init__()


@_auto_context
@dataclass
class SessionAwaitingApproval(SessionError):
    """Session is blocked waiting for human approval.

    Raised when attempting operations while approval is pending.
    """

    error_code: str = "SESSION_AWAITING_APPROVAL"
    http_status: int = 409
    session_id: str = ""
    approval_id: str = ""

    def __post_init__(self) -> None:
        if not self.message:
            self.message = f"session awaiting approval: {self.session_id}"
        super().__post_init__()


# =============================================================================
# Recovery Errors
# =============================================================================


@_auto_context
@dataclass
class RecoveryBlocked(SessionError):
    """Session recovery blocked - requires human review.

    Raised when session cannot be automatically resumed due to
    unpaired tool calls or compactions that need human review.
    """

    error_code: str = "RECOVERY_BLOCKED"
    http_status: int = 409
    state: ResumeState | None = None
    tool_call_ids: tuple[str, ...] = ()
    compaction_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.message:
            state_val = self.state.value if self.state else "unknown"
            self.message = f"recovery blocked: {state_val}"
        super().__post_init__()


# =============================================================================
# Configuration Errors
# =============================================================================


@_auto_context
@dataclass
class ToolInvokeNotConfigured(SessionError):
    """Tool invocation not configured for this session.

    Raised when attempting tool invocation without proper
    composition or tool provider configuration.
    """

    error_code: str = "TOOL_INVOKE_NOT_CONFIGURED"
    http_status: int = 501
    session_id: str = ""

    def __post_init__(self) -> None:
        if not self.message:
            self.message = "tool invocation not configured"
        super().__post_init__()


@_auto_context
@dataclass
class SubagentNotConfigured(SessionError):
    """Subagent operations not configured for this session.

    Raised when attempting subagent spawn without proper
    transport configuration.
    """

    error_code: str = "SUBAGENT_NOT_CONFIGURED"
    http_status: int = 501
    session_id: str = ""

    def __post_init__(self) -> None:
        if not self.message:
            self.message = "subagent operations not configured"
        super().__post_init__()


@_auto_context
@dataclass
class MemoryNotConfigured(SessionError):
    """Memory operations not configured for this session.

    Raised when attempting compaction/recall without proper
    memory provider configuration.
    """

    error_code: str = "MEMORY_NOT_CONFIGURED"
    http_status: int = 501
    session_id: str = ""

    def __post_init__(self) -> None:
        if not self.message:
            self.message = "memory operations not configured"
        super().__post_init__()
