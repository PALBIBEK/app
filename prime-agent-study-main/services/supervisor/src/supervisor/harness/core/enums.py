"""Harness enumerations using StrEnum for JSON serialization.

All harness enums defined here for consistent imports and avoiding
circular dependencies. Types use StrEnum for:
- JSON serialization (values are strings)
- Type safety at compile time
- Clear documentation of valid values

Reference: catalog/core/types.py pattern.
"""

from __future__ import annotations

from enum import StrEnum

__all__ = [
    "ApprovalOnTimeout",
    "GuardBehavior",
    "HarnessStatus",
    "RecoveryAction",
    "RuntimeMode",
    "ToolClass",
]


class HarnessStatus(StrEnum):
    """Lifecycle status of a harness version.

    - DRAFT: Under development, not available for session creation
    - ACTIVE: Available for new sessions
    - DEPRECATED: Existing sessions allowed, new sessions blocked
    """

    DRAFT = "draft"
    ACTIVE = "active"
    DEPRECATED = "deprecated"


class RuntimeMode(StrEnum):
    """Session execution mode.

    - STICKY: Long-lived session with persistent sandbox
    - STATELESS: Ephemeral session without sandbox persistence
    """

    STICKY = "sticky"
    STATELESS = "stateless"


class ToolClass(StrEnum):
    """Tool classification for recovery policy.

    Determines automatic recovery behavior after failures:
    - PURE: Read-only, no side effects (safe to auto-resume)
    - IDEMPOTENT: Same result if repeated (safe to auto-resume)
    - TRANSACTIONAL: Atomic operation (needs review)
    - NON_ATOMIC: May leave partial state (fail hard)
    """

    PURE = "pure"
    IDEMPOTENT = "idempotent"
    TRANSACTIONAL = "transactional"
    NON_ATOMIC = "non_atomic"


class RecoveryAction(StrEnum):
    """Recovery behavior on tool failure.

    - FAIL: Stop execution, report error
    - NEEDS_REVIEW: Pause for human review before resuming
    - AUTO_RESUME: Automatically retry after transient failure
    """

    FAIL = "fail"
    NEEDS_REVIEW = "needs_review"
    AUTO_RESUME = "auto_resume"


class GuardBehavior(StrEnum):
    """Guard rail response behavior.

    - ABORT: Stop execution when guard triggers
    - WARN: Log warning but continue execution
    - OFF: Disable this guard check
    """

    ABORT = "abort"
    WARN = "warn"
    OFF = "off"


class ApprovalOnTimeout(StrEnum):
    """Approval timeout behavior.

    - DENY: Reject operation if approval times out
    - ALLOW: Approve operation if approval times out
    """

    DENY = "deny"
    ALLOW = "allow"


# YAML aliases accepted at parse time for backward compatibility.
RECOVERY_ACTION_ALIASES: dict[str, RecoveryAction] = {
    "fail": RecoveryAction.FAIL,
    "needs_review": RecoveryAction.NEEDS_REVIEW,
    "verify_then_resume": RecoveryAction.NEEDS_REVIEW,
    "auto_resume": RecoveryAction.AUTO_RESUME,
}
