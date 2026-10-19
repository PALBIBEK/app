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

from decimal import Decimal
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from supervisor.policy.types import BudgetState


__all__ = [
    "ApprovalAlreadyResponded",
    "ApprovalError",
    "ApprovalNotFound",
    "ApprovalTimedOut",
    "BudgetExceeded",
    "CommandDenied",
    "CommandRequiresApproval",
    "CostQuotaExceeded",
    "InvalidApprovalChoice",
    "PolicyError",
    "QuotaExceeded",
    "SandboxQuotaExceeded",
    "SessionQuotaExceeded",
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

# =============================================================================
# Guard Errors
# =============================================================================

# =============================================================================
# Audit Errors
# =============================================================================

