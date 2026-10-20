"""Shared types for policy subsystem — enums, dataclasses, constants.

All policy types are defined here for consistent imports and avoiding
circular dependencies. Types use:
- StrEnum for string-based enums (JSON-serializable)
- Decimal for monetary values (no floating-point errors)
- frozen dataclasses with slots for immutability and memory efficiency
- Union types with None for optional fields

Reference: FastAPI best practices, Pydantic v2 patterns.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import Any, Final, Literal

__all__ = [
    # Constants
    "ALLOW_CHOICES",
    "DEFAULT_APPROVAL_TIMEOUT_S",
    "DEFAULT_RATE_WINDOW_MS",
    "DEFAULT_STALL_ITERATIONS",
    "Approval",
    "ApprovalContext",
    "ApprovalDefault",
    "ApprovalGrant",
    "ApprovalScope",
    # Enums
    "ApprovalStatus",
    "AuditAction",
    "AuditEvent",
    # Dataclasses
    "BudgetSpec",
    "BudgetState",
    "CommandAction",
    "CommandDecision",
    "CommandPolicyMode",
    "CommandRule",
    "FileEffects",
    "GuardAction",
    "GuardConfig",
    "GuardDetector",
    "GuardVerdict",
    "QuotaLimits",
    "QuotaState",
    "RateDecision",
    "RateDecisionStatus",
    "StallSignal",
    "StallState",
    "is_allow_choice",
]


# =============================================================================
# Constants
# =============================================================================

ALLOW_CHOICES: Final[frozenset[str]] = frozenset({
    "yes", "allow", "approve", "ok", "y",
})


def is_allow_choice(choice: str | None) -> bool:
    """True if an approval choice is allow-shaped (e.g. "allow", "yes")."""
    return choice is not None and choice.strip().lower() in ALLOW_CHOICES


DEFAULT_APPROVAL_TIMEOUT_S: Final[int] = 300
DEFAULT_RATE_WINDOW_MS: Final[int] = 60_000
DEFAULT_STALL_ITERATIONS: Final[int] = 10


# =============================================================================
# Approval Enums
# =============================================================================

class ApprovalStatus(StrEnum):
    """Status of an approval request."""
    PENDING = "pending"
    APPROVED = "approved"
    DENIED = "denied"
    TIMED_OUT = "timed_out"


class ApprovalScope(StrEnum):
    """Scope of an approval grant (QM pattern)."""
    ONCE = "once"           # Single use
    SESSION = "session"     # Valid for current session
    ALWAYS = "always"       # Persisted grant


# =============================================================================
# Guard Enums
# =============================================================================

class GuardAction(StrEnum):
    """Action to take when guard trips."""
    ABORT = "abort"
    WARN = "warn"
    OFF = "off"


class GuardDetector(StrEnum):
    """Type of guard that detected an issue."""
    REPETITION = "repetition"
    OSCILLATION = "oscillation"
    STALL = "stall"


class StallSignal(StrEnum):
    """Signal that triggered stall detection (QM 3-signal pattern)."""
    TIME = "time"           # Total time budget exceeded
    ROUNDS = "rounds"       # Too many stalled rounds
    HEARTBEAT = "heartbeat" # No heartbeat received
    QUIET = "quiet"         # Active but no progress


# =============================================================================
# Sandbox Policy Enums
# =============================================================================

class FileEffects(StrEnum):
    """File system access level for sandbox."""
    READ_ONLY = "read_only"
    WORKSPACE_WRITE = "workspace_write"
    DANGER_FULL_ACCESS = "danger_full_access"


class ApprovalDefault(StrEnum):
    """Default approval behavior for sandbox."""
    ASK = "ask"
    NEVER = "never"
    ALWAYS = "always"


# =============================================================================
# Command Policy Enums
# =============================================================================

class CommandAction(StrEnum):
    """Action to take on a command match."""
    ALLOW = "allow"
    DENY = "deny"
    REQUIRE_APPROVAL = "require_approval"


class CommandPolicyMode(StrEnum):
    """Mode for command policy evaluation."""
    DENYLIST = "denylist"   # Allow all except matches
    ALLOWLIST = "allowlist"  # Deny all except matches


# =============================================================================
# Audit Enums
# =============================================================================

class AuditAction(StrEnum):
    """Actions logged to audit trail."""
    APPROVAL_REQUESTED = "approval_requested"
    APPROVAL_GRANTED = "approval_granted"
    APPROVAL_DENIED = "approval_denied"
    APPROVAL_TIMED_OUT = "approval_timed_out"
    BUDGET_EXCEEDED = "budget_exceeded"
    QUOTA_EXCEEDED = "quota_exceeded"
    RATE_LIMITED = "rate_limited"
    COMMAND_BLOCKED = "command_blocked"
    COMMAND_APPROVAL_REQUIRED = "command_approval_required"
    LOOP_DETECTED = "loop_detected"
    STALL_DETECTED = "stall_detected"
    GRANT_CREATED = "grant_created"
    GRANT_REVOKED = "grant_revoked"


# =============================================================================
# Rate Limiting Enums
# =============================================================================

class RateDecisionStatus(StrEnum):
    """Result of rate limit check."""
    ALLOWED = "allowed"
    LIMITED = "limited"


# =============================================================================
# Budget Types
# =============================================================================

@dataclass(frozen=True, slots=True)
class BudgetSpec:
    """Budget limits specification from manifest."""
    max_cost_per_session: Decimal | None = None
    max_tokens_per_turn: int | None = None

    @classmethod
    def from_manifest(cls, spec: Any) -> BudgetSpec | None:
        """A harness manifest's budget (costs as floats) in exact Decimal dollars."""
        if spec is None:
            return None
        cost = spec.max_cost_per_session
        return cls(
            max_cost_per_session=None if cost is None else Decimal(str(cost)),
            max_tokens_per_turn=spec.max_tokens_per_turn,
        )


@dataclass(frozen=True, slots=True)
class BudgetState:
    """Current budget state for a session."""
    session_id: str
    total_cost_usd: Decimal
    tokens_this_turn: int
    max_cost_usd: Decimal | None
    max_tokens_per_turn: int | None
    exceeded: bool
    exceeded_reason: str | None = None


# =============================================================================
# Guard Types
# =============================================================================

@dataclass(frozen=True, slots=True)
class GuardConfig:
    """Configuration for loop/stall guards."""
    repetition: GuardAction = GuardAction.ABORT
    oscillation: GuardAction = GuardAction.ABORT
    stall_iterations: int = DEFAULT_STALL_ITERATIONS
    window_size: int = 32
    repetition_threshold: int = 3


@dataclass(frozen=True, slots=True)
class GuardVerdict:
    """Result of guard observation."""
    tripped: bool
    detector: GuardDetector | None = None
    action: GuardAction = GuardAction.OFF
    detail: str | None = None
    call_hash: str | None = None
    tool_name: str | None = None
    call_count: int = 0


@dataclass(frozen=True, slots=True)
class StallState:
    """Enhanced 3-signal stall detection state (QM pattern).

    Tracks three signals:
    1. TIME: Total run time budget exceeded
    2. ROUNDS: Too many consecutive stalled rounds
    3. HEARTBEAT: No heartbeat received within threshold

    Plus QUIET detection: heartbeat ok but no progress.
    """
    session_id: str
    stalled: bool
    signal: StallSignal | None
    stalled_rounds: int
    time_elapsed_ms: int
    last_heartbeat_ms: int
    last_progress_ms: int
    auto_waived: bool = False
    # Legacy fields for backward compatibility
    quiet_since_ms: int | None = None
    is_stalled: bool = False  # Alias for stalled
    stall_duration_ms: int | None = None


# =============================================================================
# Approval Types
# =============================================================================

@dataclass(frozen=True, slots=True)
class Approval:
    """Human-in-the-loop approval request."""
    approval_id: str
    session_id: str
    turn_id: str | None
    prompt: str
    options: tuple[str, ...]
    status: ApprovalStatus
    choice: str | None
    timeout_s: int
    created_at_ms: int
    harness_request_id: str | None = None
    responded_by: str | None = None
    responded_at_ms: int | None = None

    @property
    def is_pending(self) -> bool:
        return self.status == ApprovalStatus.PENDING

    @property
    def is_allow(self) -> bool:
        """True if choice is an allow-shaped response."""
        if self.choice is None:
            return False
        return self.choice.strip().lower() in ALLOW_CHOICES


@dataclass(frozen=True, slots=True)
class ApprovalGrant:
    """Persisted approval grant (QM once/session/always pattern)."""
    grant_id: str
    principal_id: str
    command_pattern: str
    scope: ApprovalScope
    session_id: str | None  # For session-scoped grants
    created_at_ms: int
    expires_at_ms: int | None = None


@dataclass(frozen=True, slots=True)
class ApprovalContext:
    """Context for approval recovery across restarts."""
    approval_id: str
    session_id: str
    turn_id: str | None
    tool_call_id: str | None
    tool_name: str | None
    arguments_hash: str | None
    state_snapshot: dict[str, Any] | None = None


# =============================================================================
# Command Policy Types
# =============================================================================

@dataclass(frozen=True, slots=True)
class CommandRule:
    """Rule for command policy evaluation."""
    pattern: str
    action: CommandAction
    reason: str
    priority: int = 0
    case_sensitive: bool = False


@dataclass(frozen=True, slots=True)
class CommandDecision:
    """Result of command policy evaluation."""
    allowed: bool
    requires_approval: bool
    rule_matched: str | None
    reason: str | None
    action: CommandAction


# =============================================================================
# Rate Limiting Types
# =============================================================================

@dataclass(frozen=True, slots=True)
class RateDecision:
    """Result of rate limit check."""
    status: RateDecisionStatus
    allowed: bool
    remaining: int
    limit: int
    retry_after_ms: int | None = None
    window_ms: int = DEFAULT_RATE_WINDOW_MS


# =============================================================================
# Audit Types
# =============================================================================

@dataclass(frozen=True, slots=True)
class AuditEvent:
    """Audit trail event."""
    event_id: str
    timestamp_ms: int
    action: AuditAction
    principal_id: str
    session_id: str | None
    resource: str | None
    status: Literal["success", "failure", "blocked"]
    detail: dict[str, Any] | None = None

    @classmethod
    def now(
        cls,
        event_id: str,
        action: AuditAction,
        principal_id: str,
        status: Literal["success", "failure", "blocked"],
        *,
        session_id: str | None = None,
        resource: str | None = None,
        detail: dict[str, Any] | None = None,
    ) -> AuditEvent:
        """Create event with current timestamp."""
        return cls(
            event_id=event_id,
            timestamp_ms=int(time.time() * 1000),
            action=action,
            principal_id=principal_id,
            session_id=session_id,
            resource=resource,
            status=status,
            detail=detail,
        )


# =============================================================================
# Quota Types
# =============================================================================

@dataclass(frozen=True, slots=True)
class QuotaLimits:
    """Tenant quota limits."""
    max_sessions_per_tenant: int | None = None
    max_sandboxes_per_tenant: int | None = None
    max_sessions_global: int | None = None
    max_cost_per_tenant: Decimal | None = None


@dataclass(frozen=True, slots=True)
class QuotaState:
    """Current quota state for a tenant."""
    tenant_id: str
    active_sessions: int
    active_sandboxes: int
    total_cost_usd: Decimal
    limits: QuotaLimits
    exceeded: bool = False
    exceeded_reason: str | None = None
