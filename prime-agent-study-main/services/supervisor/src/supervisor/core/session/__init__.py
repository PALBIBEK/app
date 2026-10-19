"""
Session management module.

Provides clean, focused components for session lifecycle:
- Domain models (Session, SessionConfig, SessionState)
- Registry (thread-safe session state)
- Lifecycle (create, terminate, fork)
- Recovery (warm/cold resume)
- Coordinator (orchestration)

Each component has a single responsibility and clear interfaces.
"""

from supervisor.core.session.errors import (
    SessionAlreadyExistsError,
    SessionBusyError,
    SessionError,
    SessionNotFoundError,
    SessionQuotaExceededError,
    SessionRecoveryError,
    SessionTerminatedError,
)
from supervisor.core.session.models import (
    Session,
    SessionConfig,
    SessionMetrics,
    SessionMode,
    SessionState,
    SessionStatus,
)

__all__ = [
    # Models
    "Session",
    "SessionConfig",
    "SessionState",
    "SessionStatus",
    "SessionMode",
    "SessionMetrics",
    # Errors
    "SessionError",
    "SessionNotFoundError",
    "SessionAlreadyExistsError",
    "SessionTerminatedError",
    "SessionBusyError",
    "SessionQuotaExceededError",
    "SessionRecoveryError",
]
