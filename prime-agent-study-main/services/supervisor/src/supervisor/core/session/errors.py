"""
Session error hierarchy.

Provides typed exceptions for all session-related failures with:
- Clear error messages
- Error codes for API responses
- Retryable flag for clients
- Structured context for debugging

Error Hierarchy:
    SessionError (base)
    ├── SessionNotFoundError (404)
    ├── SessionAlreadyExistsError (409)
    ├── SessionTerminatedError (410)
    ├── SessionBusyError (423)
    ├── SessionQuotaExceededError (429)
    ├── SessionRecoveryError (500)
    ├── SessionStateError (400)
    └── SessionTimeoutError (504)

Usage:
    try:
        await coordinator.send(session_id, message)
    except SessionNotFoundError:
        return HTTPException(404, "Session not found")
    except SessionBusyError as e:
        return HTTPException(423, f"Session busy: {e.reason}")
    except SessionError as e:
        return HTTPException(e.status_code, str(e))
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class SessionError(Exception):
    """
    Base exception for session operations.

    Attributes:
        message: Human-readable error message.
        session_id: Affected session (if known).
        error_code: Machine-readable error code.
        status_code: HTTP status code for API responses.
        retryable: Whether client should retry.
        context: Additional debugging context.
    """

    message: str
    session_id: str | None = None
    error_code: str = "SESSION_ERROR"
    status_code: int = 500
    retryable: bool = False
    context: dict[str, Any] = field(default_factory=dict)

    def __str__(self) -> str:
        if self.session_id:
            return f"[{self.error_code}] {self.message} (session={self.session_id})"
        return f"[{self.error_code}] {self.message}"

    def __post_init__(self) -> None:
        """Initialize base Exception."""
        super().__init__(str(self))

    def to_dict(self) -> dict[str, Any]:
        """Convert to API response format."""
        return {
            "error": self.error_code,
            "message": self.message,
            "session_id": self.session_id,
            "retryable": self.retryable,
            **self.context,
        }


@dataclass
class SessionNotFoundError(SessionError):
    """Session does not exist or has been deleted."""

    message: str = "Session not found"
    error_code: str = "SESSION_NOT_FOUND"
    status_code: int = 404
    retryable: bool = False


@dataclass
class SessionAlreadyExistsError(SessionError):
    """Session with this ID already exists."""

    message: str = "Session already exists"
    error_code: str = "SESSION_ALREADY_EXISTS"
    status_code: int = 409
    retryable: bool = False


@dataclass
class SessionTerminatedError(SessionError):
    """Session has been terminated and cannot be used."""

    message: str = "Session has been terminated"
    error_code: str = "SESSION_TERMINATED"
    status_code: int = 410
    retryable: bool = False
    termination_reason: str | None = None

    def __post_init__(self) -> None:
        if self.termination_reason:
            self.context["termination_reason"] = self.termination_reason
        super().__post_init__()


@dataclass
class SessionBusyError(SessionError):
    """Session is currently processing and cannot accept new requests."""

    message: str = "Session is busy"
    error_code: str = "SESSION_BUSY"
    status_code: int = 423
    retryable: bool = True
    reason: str = "processing"

    def __post_init__(self) -> None:
        self.context["reason"] = self.reason
        super().__post_init__()


@dataclass
class SessionQuotaExceededError(SessionError):
    """Quota exceeded (max sessions, sandboxes, tokens, etc.)."""

    message: str = "Quota exceeded"
    error_code: str = "QUOTA_EXCEEDED"
    status_code: int = 429
    retryable: bool = True
    quota_type: str = "unknown"
    current: int = 0
    limit: int = 0

    def __post_init__(self) -> None:
        self.context["quota_type"] = self.quota_type
        self.context["current"] = self.current
        self.context["limit"] = self.limit
        super().__post_init__()


@dataclass
class SessionRecoveryError(SessionError):
    """Session cannot be recovered (corrupted state, unpaired tools, etc.)."""

    message: str = "Session recovery failed"
    error_code: str = "RECOVERY_FAILED"
    status_code: int = 500
    retryable: bool = False
    recovery_state: str = "unknown"
    unpaired_count: int = 0

    def __post_init__(self) -> None:
        self.context["recovery_state"] = self.recovery_state
        self.context["unpaired_count"] = self.unpaired_count
        super().__post_init__()


@dataclass
class SessionStateError(SessionError):
    """Invalid state transition or operation."""

    message: str = "Invalid session state"
    error_code: str = "INVALID_STATE"
    status_code: int = 400
    retryable: bool = False
    current_state: str = "unknown"
    attempted_operation: str = "unknown"

    def __post_init__(self) -> None:
        self.context["current_state"] = self.current_state
        self.context["attempted_operation"] = self.attempted_operation
        super().__post_init__()


@dataclass
class SessionTimeoutError(SessionError):
    """Operation timed out."""

    message: str = "Session operation timed out"
    error_code: str = "TIMEOUT"
    status_code: int = 504
    retryable: bool = True
    operation: str = "unknown"
    timeout_seconds: float = 0.0

    def __post_init__(self) -> None:
        self.context["operation"] = self.operation
        self.context["timeout_seconds"] = self.timeout_seconds
        super().__post_init__()


@dataclass
class SessionAuthorizationError(SessionError):
    """Authorization failed for session operation."""

    message: str = "Not authorized"
    error_code: str = "UNAUTHORIZED"
    status_code: int = 403
    retryable: bool = False
    required_grant: str | None = None

    def __post_init__(self) -> None:
        if self.required_grant:
            self.context["required_grant"] = self.required_grant
        super().__post_init__()


@dataclass
class SessionValidationError(SessionError):
    """Request validation failed."""

    message: str = "Validation failed"
    error_code: str = "VALIDATION_ERROR"
    status_code: int = 400
    retryable: bool = False
    field: str | None = None
    constraint: str | None = None

    def __post_init__(self) -> None:
        if self.field:
            self.context["field"] = self.field
        if self.constraint:
            self.context["constraint"] = self.constraint
        super().__post_init__()
