"""Exception hierarchy for policy subsystem.

All policy-related exceptions inherit from PolicyError for unified
error handling. Each exception carries structured data for:
- API error responses
- Audit logging
- Retry logic (retryable flag)

Design principles:
- Rich context in exception attributes
- No bare Exception raises
- Retryable flag for transient failures
- Structured error codes for API responses
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from supervisor.policy.types import BudgetState, QuotaLimits


__all__ = [
    # Base
    "PolicyError",
    # Budget
    "BudgetExceeded",
    "BudgetCostExceeded",
    "BudgetTokensExceeded",
    # Quota
    "QuotaExceeded",
    "SessionQuotaExceeded",
    "SandboxQuotaExceeded",
    "CostQuotaExceeded",
    # Approval
    "ApprovalError",
    "ApprovalNotFound",
    "ApprovalAlreadyResponded",
    "ApprovalTimedOut",
    "InvalidApprovalChoice",
    "ApprovalRecoveryFailed",
    # Command
    "CommandDenied",
    "CommandRequiresApproval",
    # Rate Limiting
    "RateLimitExceeded",
    # Guard
    "GuardTripped",
    "LoopDetected",
    "StallDetected",
    # Audit
    "AuditWriteFailed",
]


# =============================================================================
# Base Error
# =============================================================================

class PolicyError(Exception):
    """Base exception for all policy errors.

    Attributes:
        error_code: Machine-readable error code for API responses.
        retryable: Whether the operation can be retried.
        detail: Additional structured context.
    """

    error_code: str = "POLICY_ERROR"
    retryable: bool = False

    def __init__(
        self,
        message: str,
        *,
        detail: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.detail = detail or {}

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for API responses."""
        return {
            "error_code": self.error_code,
            "message": str(self),
            "retryable": self.retryable,
            "detail": self.detail,
        }


# =============================================================================
# Budget Errors
# =============================================================================

class BudgetExceeded(PolicyError):
    """Cost or token limit exceeded."""

    error_code = "BUDGET_EXCEEDED"

    def __init__(
        self,
        message: str,
        *,
        session_id: str,
        current_cost: Decimal,
        limit_cost: Decimal | None = None,
        current_tokens: int = 0,
        limit_tokens: int | None = None,
    ) -> None:
        super().__init__(
            message,
            detail={
                "session_id": session_id,
                "current_cost": str(current_cost),
                "limit_cost": str(limit_cost) if limit_cost else None,
                "current_tokens": current_tokens,
                "limit_tokens": limit_tokens,
            },
        )
        self.session_id = session_id
        self.current_cost = current_cost
        self.limit_cost = limit_cost
        self.current_tokens = current_tokens
        self.limit_tokens = limit_tokens

    @classmethod
    def from_state(cls, state: BudgetState) -> BudgetExceeded:
        """The error for a tracker state that reports ``exceeded``."""
        return cls(
            state.exceeded_reason or "budget exceeded",
            session_id=state.session_id,
            current_cost=state.total_cost_usd,
            limit_cost=state.max_cost_usd,
            current_tokens=state.tokens_this_turn,
            limit_tokens=state.max_tokens_per_turn,
        )


class BudgetCostExceeded(BudgetExceeded):
    """Session cost limit exceeded."""

    error_code = "BUDGET_COST_EXCEEDED"

    def __init__(
        self,
        session_id: str,
        current_cost: Decimal,
        limit_cost: Decimal,
    ) -> None:
        super().__init__(
            f"max_cost_per_session exceeded: cost={current_cost} limit={limit_cost}",
            session_id=session_id,
            current_cost=current_cost,
            limit_cost=limit_cost,
        )


class BudgetTokensExceeded(BudgetExceeded):
    """Per-turn token limit exceeded."""

    error_code = "BUDGET_TOKENS_EXCEEDED"

    def __init__(
        self,
        session_id: str,
        current_tokens: int,
        limit_tokens: int,
        current_cost: Decimal = Decimal("0"),
    ) -> None:
        super().__init__(
            f"max_tokens_per_turn exceeded: tokens={current_tokens} limit={limit_tokens}",
            session_id=session_id,
            current_cost=current_cost,
            current_tokens=current_tokens,
            limit_tokens=limit_tokens,
        )


# =============================================================================
# Quota Errors
# =============================================================================

class QuotaExceeded(PolicyError):
    """Tenant quota exceeded."""

    error_code = "QUOTA_EXCEEDED"

    def __init__(
        self,
        message: str,
        *,
        tenant_id: str,
        limit_name: str,
        current: int | Decimal,
        limit: int | Decimal,
    ) -> None:
        super().__init__(
            message,
            detail={
                "tenant_id": tenant_id,
                "limit_name": limit_name,
                "current": str(current),
                "limit": str(limit),
            },
        )
        self.tenant_id = tenant_id
        self.limit_name = limit_name
        self.current = current
        self.limit = limit


class SessionQuotaExceeded(QuotaExceeded):
    """Session quota exceeded for tenant."""

    error_code = "SESSION_QUOTA_EXCEEDED"

    def __init__(
        self,
        tenant_id: str,
        current: int,
        limit: int,
        *,
        is_global: bool = False,
    ) -> None:
        limit_name = "max_sessions_global" if is_global else "max_sessions_per_tenant"
        super().__init__(
            f"{limit_name} exceeded: {current}>={limit}",
            tenant_id=tenant_id,
            limit_name=limit_name,
            current=current,
            limit=limit,
        )
        self.is_global = is_global


class SandboxQuotaExceeded(QuotaExceeded):
    """Sandbox quota exceeded for tenant."""

    error_code = "SANDBOX_QUOTA_EXCEEDED"

    def __init__(
        self,
        tenant_id: str,
        current: int,
        limit: int,
    ) -> None:
        super().__init__(
            f"max_sandboxes_per_tenant exceeded: {current}>={limit}",
            tenant_id=tenant_id,
            limit_name="max_sandboxes_per_tenant",
            current=current,
            limit=limit,
        )


class CostQuotaExceeded(QuotaExceeded):
    """Cost quota exceeded for tenant."""

    error_code = "COST_QUOTA_EXCEEDED"

    def __init__(
        self,
        tenant_id: str,
        current: Decimal,
        limit: Decimal,
    ) -> None:
        super().__init__(
            f"max_cost_per_tenant exceeded: cost={current} limit={limit}",
            tenant_id=tenant_id,
            limit_name="max_cost_per_tenant",
            current=current,
            limit=limit,
        )


# =============================================================================
# Approval Errors
# =============================================================================

class ApprovalError(PolicyError):
    """Base for approval-related errors."""

    error_code = "APPROVAL_ERROR"


class ApprovalNotFound(ApprovalError):
    """Unknown approval ID."""

    error_code = "APPROVAL_NOT_FOUND"

    def __init__(self, approval_id: str) -> None:
        super().__init__(
            f"unknown approval_id={approval_id!r}",
            detail={"approval_id": approval_id},
        )
        self.approval_id = approval_id


class ApprovalAlreadyResponded(ApprovalError):
    """Approval was already responded to."""

    error_code = "APPROVAL_ALREADY_RESPONDED"

    def __init__(
        self,
        approval_id: str,
        *,
        current_choice: str | None = None,
    ) -> None:
        super().__init__(
            f"approval {approval_id!r} already responded",
            detail={
                "approval_id": approval_id,
                "current_choice": current_choice,
            },
        )
        self.approval_id = approval_id
        self.current_choice = current_choice


class ApprovalTimedOut(ApprovalError):
    """Approval request timed out."""

    error_code = "APPROVAL_TIMED_OUT"

    def __init__(self, approval_id: str) -> None:
        super().__init__(
            f"approval {approval_id!r} timed out",
            detail={"approval_id": approval_id},
        )
        self.approval_id = approval_id


class InvalidApprovalChoice(ApprovalError):
    """Choice not in allowed options."""

    error_code = "INVALID_APPROVAL_CHOICE"

    def __init__(
        self,
        approval_id: str,
        choice: str,
        options: tuple[str, ...],
    ) -> None:
        super().__init__(
            f"choice {choice!r} not in options {list(options)!r}",
            detail={
                "approval_id": approval_id,
                "choice": choice,
                "options": list(options),
            },
        )
        self.approval_id = approval_id
        self.choice = choice
        self.options = options


class ApprovalRecoveryFailed(ApprovalError):
    """Failed to recover approval context after restart."""

    error_code = "APPROVAL_RECOVERY_FAILED"
    retryable = True

    def __init__(
        self,
        approval_id: str,
        reason: str,
    ) -> None:
        super().__init__(
            f"failed to recover approval {approval_id!r}: {reason}",
            detail={"approval_id": approval_id, "reason": reason},
        )
        self.approval_id = approval_id
        self.reason = reason


# =============================================================================
# Command Errors
# =============================================================================

class CommandDenied(PolicyError):
    """Command blocked by policy."""

    error_code = "COMMAND_DENIED"

    def __init__(
        self,
        command: str,
        rule: str,
        reason: str,
    ) -> None:
        super().__init__(
            f"command blocked: {reason}",
            detail={
                "command": command,
                "rule": rule,
                "reason": reason,
            },
        )
        self.command = command
        self.rule = rule
        self.reason = reason


class CommandRequiresApproval(PolicyError):
    """Command requires explicit approval."""

    error_code = "COMMAND_REQUIRES_APPROVAL"

    def __init__(
        self,
        command: str,
        rule: str,
        reason: str,
    ) -> None:
        super().__init__(
            f"command requires approval: {reason}",
            detail={
                "command": command,
                "rule": rule,
                "reason": reason,
            },
        )
        self.command = command
        self.rule = rule
        self.reason = reason


# =============================================================================
# Rate Limiting Errors
# =============================================================================

class RateLimitExceeded(PolicyError):
    """Rate limit hit."""

    error_code = "RATE_LIMIT_EXCEEDED"
    retryable = True

    def __init__(
        self,
        principal_id: str,
        retry_after_ms: int,
        *,
        limit: int | None = None,
        window_ms: int | None = None,
    ) -> None:
        super().__init__(
            f"rate limit exceeded for {principal_id!r}, retry after {retry_after_ms}ms",
            detail={
                "principal_id": principal_id,
                "retry_after_ms": retry_after_ms,
                "limit": limit,
                "window_ms": window_ms,
            },
        )
        self.principal_id = principal_id
        self.retry_after_ms = retry_after_ms
        self.limit = limit
        self.window_ms = window_ms


# =============================================================================
# Guard Errors
# =============================================================================

class GuardTripped(PolicyError):
    """Base for guard-triggered errors."""

    error_code = "GUARD_TRIPPED"

    def __init__(
        self,
        message: str,
        *,
        session_id: str,
        detector: str,
        tool_name: str | None = None,
        call_hash: str | None = None,
    ) -> None:
        super().__init__(
            message,
            detail={
                "session_id": session_id,
                "detector": detector,
                "tool_name": tool_name,
                "call_hash": call_hash,
            },
        )
        self.session_id = session_id
        self.detector = detector
        self.tool_name = tool_name
        self.call_hash = call_hash


class LoopDetected(GuardTripped):
    """Loop pattern detected (repetition or oscillation)."""

    error_code = "LOOP_DETECTED"

    def __init__(
        self,
        session_id: str,
        detector: str,
        tool_name: str,
        call_hash: str,
        detail_msg: str,
    ) -> None:
        super().__init__(
            detail_msg,
            session_id=session_id,
            detector=detector,
            tool_name=tool_name,
            call_hash=call_hash,
        )


class StallDetected(GuardTripped):
    """Stall detected — no progress for too many iterations."""

    error_code = "STALL_DETECTED"

    def __init__(
        self,
        session_id: str,
        iterations: int,
        tool_name: str | None = None,
    ) -> None:
        super().__init__(
            f"stall: no progress for {iterations} tool iterations",
            session_id=session_id,
            detector="stall",
            tool_name=tool_name,
        )
        self.iterations = iterations


# =============================================================================
# Audit Errors
# =============================================================================

class AuditWriteFailed(PolicyError):
    """Failed to write audit event."""

    error_code = "AUDIT_WRITE_FAILED"
    retryable = True

    def __init__(
        self,
        event_id: str,
        reason: str,
    ) -> None:
        super().__init__(
            f"failed to write audit event {event_id!r}: {reason}",
            detail={"event_id": event_id, "reason": reason},
        )
        self.event_id = event_id
        self.reason = reason
