"""Protocol interfaces for the policy subsystem: rate limiting and audit logging.

Implementations are swappable (memory, Postgres) and easy to fake in tests.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from supervisor.policy.types import (
        AuditEvent,
        RateDecision,
    )


__all__ = [
    "AuditLoggerProtocol",
    "RateLimiterProtocol",
]


# =============================================================================
# Budget Protocols
# =============================================================================



# =============================================================================
# Approval Protocols
# =============================================================================



# =============================================================================
# Guard Protocols
# =============================================================================



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



# =============================================================================
# Command Policy Protocols
# =============================================================================



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
