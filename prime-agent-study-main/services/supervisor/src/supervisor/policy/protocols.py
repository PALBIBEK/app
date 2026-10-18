"""Protocol interfaces for policy subsystem — dependency injection.

All policy components are defined as Protocol classes to enable:
- Dependency injection in FastAPI
- Easy mocking in tests
- Swappable implementations (memory vs Postgres)
- Clear interface contracts

Protocol classes use @runtime_checkable for isinstance() support.

Usage:
    def my_endpoint(
        budget: BudgetTrackerProtocol = Depends(get_budget_tracker),
    ) -> Response:
        state = await budget.check(session_id)
        ...
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from supervisor.policy.types import (
        Approval,
        ApprovalContext,
        ApprovalGrant,
        ApprovalScope,
        AuditEvent,
        BudgetSpec,
        BudgetState,
        CommandDecision,
        GuardConfig,
        GuardVerdict,
        QuotaLimits,
        QuotaState,
        RateDecision,
        StallState,
    )


__all__ = [
    "BudgetTrackerProtocol",
    "CostEstimatorProtocol",
    "ApprovalBrokerProtocol",
    "ApprovalGrantStoreProtocol",
    "ApprovalRecoveryProtocol",
    "LoopGuardProtocol",
    "StallGuardProtocol",
    "RateLimiterProtocol",
    "QuotaEnforcerProtocol",
    "CommandPolicyProtocol",
    "AuditLoggerProtocol",
]


# =============================================================================
# Budget Protocols
# =============================================================================

@runtime_checkable
class BudgetTrackerProtocol(Protocol):
    """Interface for budget tracking.

    Implementations:
    - InMemoryBudgetTracker: For single-process deployments
    - PostgresBudgetTracker: For distributed deployments
    """

    def configure(self, session_id: str, spec: BudgetSpec | None) -> None:
        """Configure budget limits for a session."""
        ...

    def begin_turn(self, session_id: str) -> None:
        """Reset per-turn token counter."""
        ...

    async def check(self, session_id: str) -> BudgetState:
        """Check current budget state (before turn)."""
        ...

    async def record(
        self,
        session_id: str,
        cost_usd: Decimal,
        tokens: int,
    ) -> BudgetState:
        """Record usage and return updated state."""
        ...


@runtime_checkable
class CostEstimatorProtocol(Protocol):
    """Interface for pre-execution cost estimation.

    Enables early rejection before expensive model calls.
    """

    def estimate_turn_cost(
        self,
        model: str,
        input_tokens: int,
        estimated_output_tokens: int,
    ) -> Decimal:
        """Estimate cost for a turn."""
        ...

    async def would_exceed_budget(
        self,
        session_id: str,
        estimated_cost: Decimal,
    ) -> bool:
        """Check if estimated cost would exceed budget."""
        ...


# =============================================================================
# Approval Protocols
# =============================================================================

@runtime_checkable
class ApprovalBrokerProtocol(Protocol):
    """Interface for approval request management.

    Handles human-in-the-loop approval workflow:
    - Create pending approvals
    - Respond to approvals
    - Expire timed-out approvals
    """

    async def request(
        self,
        session_id: str,
        turn_id: str | None,
        prompt: str,
        options: list[str],
        timeout_s: int,
        *,
        harness_request_id: str | None = None,
    ) -> str:
        """Create pending approval, return approval_id."""
        ...

    async def respond(self, approval_id: str, choice: str) -> Approval:
        """Respond to approval with choice."""
        ...

    async def get(self, approval_id: str) -> Approval | None:
        """Get approval by ID."""
        ...

    async def pending(self, session_id: str) -> list[Approval]:
        """List pending approvals for session."""
        ...

    async def expire_due(
        self,
        *,
        now_ms: int | None = None,
        default_choice: str = "deny",
    ) -> list[Approval]:
        """Expire overdue approvals."""
        ...


@runtime_checkable
class ApprovalGrantStoreProtocol(Protocol):
    """Interface for approval grant management (QM pattern).

    Grants allow bypassing approval for matching commands:
    - ONCE: Single use, auto-revokes
    - SESSION: Valid for session lifetime
    - ALWAYS: Persisted until explicit revoke
    """

    async def grant(
        self,
        principal_id: str,
        command_pattern: str,
        scope: ApprovalScope,
        *,
        session_id: str | None = None,
        expires_at_ms: int | None = None,
    ) -> ApprovalGrant:
        """Create new approval grant."""
        ...

    async def check_grant(
        self,
        principal_id: str,
        command: str,
        session_id: str,
    ) -> ApprovalGrant | None:
        """Check if command has matching grant."""
        ...

    async def revoke(self, grant_id: str) -> bool:
        """Revoke a grant, return True if found."""
        ...

    async def list_grants(
        self,
        principal_id: str,
        *,
        session_id: str | None = None,
        limit: int = 100,
    ) -> list[ApprovalGrant]:
        """List grants for principal."""
        ...


@runtime_checkable
class ApprovalRecoveryProtocol(Protocol):
    """Interface for approval context recovery across restarts.

    Stores context needed to resume approval workflow after restart.
    """

    async def store_context(
        self,
        approval_id: str,
        context: ApprovalContext,
    ) -> None:
        """Store context for recovery."""
        ...

    async def recover_context(
        self,
        approval_id: str,
    ) -> ApprovalContext | None:
        """Recover stored context."""
        ...

    async def clear_context(self, approval_id: str) -> None:
        """Clear stored context after completion."""
        ...


# =============================================================================
# Guard Protocols
# =============================================================================

@runtime_checkable
class LoopGuardProtocol(Protocol):
    """Interface for loop/repetition detection.

    Detects:
    - Repetition: N consecutive identical tool calls
    - Oscillation: A-B-A-B pattern
    """

    def configure(self, session_id: str, config: GuardConfig) -> None:
        """Configure guard for session."""
        ...

    def observe(
        self,
        session_id: str,
        tool_name: str,
        args_hash: str,
    ) -> GuardVerdict:
        """Observe tool call and return verdict."""
        ...

    def reset(self, session_id: str) -> None:
        """Reset state for session (on progress)."""
        ...


@runtime_checkable
class StallGuardProtocol(Protocol):
    """Interface for heartbeat-based stall detection (QM pattern).

    Detects operations that haven't made progress via heartbeats.
    """

    async def heartbeat(self, session_id: str) -> None:
        """Record heartbeat from session."""
        ...

    def check_stall(self, session_id: str) -> StallState:
        """Check if session is stalled."""
        ...

    def reset(self, session_id: str) -> None:
        """Reset stall state for session."""
        ...


# =============================================================================
# Rate Limiting Protocols
# =============================================================================

@runtime_checkable
class RateLimiterProtocol(Protocol):
    """Interface for rate limiting.

    Implementations:
    - MemoryRateLimiter: For single-process
    - PostgresRateLimiter: For distributed deployments
    """

    async def check(self, principal_id: str) -> RateDecision:
        """Check if request is allowed."""
        ...

    async def record(self, principal_id: str) -> None:
        """Record request for rate limiting."""
        ...

    async def check_and_record(self, principal_id: str) -> RateDecision:
        """Atomic check and record."""
        ...


# =============================================================================
# Quota Protocols
# =============================================================================

@runtime_checkable
class QuotaEnforcerProtocol(Protocol):
    """Interface for tenant quota enforcement.

    Enforces:
    - Max sessions per tenant
    - Max sandboxes per tenant
    - Max sessions global
    - Max cost per tenant
    """

    async def check_create(
        self,
        tenant_id: str,
        *,
        requires_sandbox: bool = False,
    ) -> QuotaState:
        """Check if session/sandbox can be created. Raises QuotaExceeded."""
        ...

    async def get_state(self, tenant_id: str) -> QuotaState:
        """Get current quota state for tenant."""
        ...


# =============================================================================
# Command Policy Protocols
# =============================================================================

@runtime_checkable
class CommandPolicyProtocol(Protocol):
    """Interface for command policy evaluation (QM pattern).

    Evaluates commands against denylist/allowlist rules.
    """

    def evaluate(self, command: str) -> CommandDecision:
        """Evaluate command against policy."""
        ...

    def add_rule(
        self,
        pattern: str,
        action: str,
        reason: str,
        *,
        priority: int = 0,
    ) -> None:
        """Add custom rule."""
        ...


# =============================================================================
# Audit Protocols
# =============================================================================

@runtime_checkable
class AuditLoggerProtocol(Protocol):
    """Interface for audit logging.

    Logs all policy decisions for compliance and debugging.
    """

    async def log(self, event: AuditEvent) -> None:
        """Log audit event."""
        ...

    async def log_once(self, key: str, event: AuditEvent) -> bool:
        """Log event only if key not seen. Returns True if logged."""
        ...

    async def query(
        self,
        *,
        action: str | None = None,
        principal_id: str | None = None,
        session_id: str | None = None,
        since_ms: int | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[AuditEvent]:
        """Query audit events."""
        ...
