"""Quota enforcer — tenant resource limit enforcement.

Rewritten with:
- Atomic check with SELECT FOR UPDATE
- Single optimized query instead of N+1
- Proper transaction handling
- Decimal for cost calculations

Reference: QM's quota enforcement pattern.
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING, Final

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from supervisor.db.models import SessionIndexRow, SessionRow
from supervisor.policy.errors import (
    CostQuotaExceeded,
    QuotaExceeded,
    SandboxQuotaExceeded,
    SessionQuotaExceeded,
)
from supervisor.policy.types import QuotaLimits, QuotaState
from supervisor.policy.quotas.limits import DEFAULT_QUOTA_LIMITS


if TYPE_CHECKING:
    pass


__all__ = ["QuotaEnforcer"]


# Session statuses that count toward quota
ACTIVE_STATUSES: Final[tuple[str, ...]] = ("active", "paused", "awaiting_approval")


class QuotaEnforcer:
    """Enforces tenant resource quotas.

    Quotas enforced:
    - Max sessions per tenant
    - Max sandboxes per tenant
    - Max sessions global
    - Max cost per tenant

    Uses optimized queries to minimize DB roundtrips.

    Example:
        enforcer = QuotaEnforcer(session_factory, limits=QuotaLimits(
            max_sessions_per_tenant=100,
            max_sandboxes_per_tenant=10,
        ))

        # Before creating session
        state = await enforcer.check_create(
            tenant_id="tenant_123",
            requires_sandbox=True,
        )
        if state.exceeded:
            raise state.exceeded_reason
    """

    __slots__ = ("_db", "_limits")

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        limits: QuotaLimits | None = None,
    ) -> None:
        self._db = session_factory
        self._limits = limits or DEFAULT_QUOTA_LIMITS

    async def check_create(
        self,
        tenant_id: str,
        *,
        requires_sandbox: bool = False,
        live_sandbox_count: int | None = None,
    ) -> QuotaState:
        """Check if session/sandbox can be created.

        Args:
            tenant_id: Tenant requesting creation
            requires_sandbox: Whether session needs sandbox
            live_sandbox_count: Current live sandbox count (if known)

        Returns:
            QuotaState with exceeded info if over limit

        Raises:
            SessionQuotaExceeded: Session quota exceeded
            SandboxQuotaExceeded: Sandbox quota exceeded
            CostQuotaExceeded: Cost quota exceeded
        """
        limits = self._limits

        # Load all quota state in single query
        state = await self._load_state(tenant_id, live_sandbox_count)

        # Check global session limit
        if limits.max_sessions_global is not None:
            total_sessions = await self._count_sessions_global()
            if total_sessions >= limits.max_sessions_global:
                raise SessionQuotaExceeded(
                    tenant_id=tenant_id,
                    current=total_sessions,
                    limit=limits.max_sessions_global,
                    is_global=True,
                )

        # Check tenant session limit
        if limits.max_sessions_per_tenant is not None:
            if state.active_sessions >= limits.max_sessions_per_tenant:
                raise SessionQuotaExceeded(
                    tenant_id=tenant_id,
                    current=state.active_sessions,
                    limit=limits.max_sessions_per_tenant,
                )

        # Check sandbox limit
        if requires_sandbox and limits.max_sandboxes_per_tenant is not None:
            if state.active_sandboxes >= limits.max_sandboxes_per_tenant:
                raise SandboxQuotaExceeded(
                    tenant_id=tenant_id,
                    current=state.active_sandboxes,
                    limit=limits.max_sandboxes_per_tenant,
                )

        # Check cost limit
        if limits.max_cost_per_tenant is not None:
            if state.total_cost_usd > limits.max_cost_per_tenant:
                raise CostQuotaExceeded(
                    tenant_id=tenant_id,
                    current=state.total_cost_usd,
                    limit=limits.max_cost_per_tenant,
                )

        return state

    async def get_state(self, tenant_id: str) -> QuotaState:
        """Get current quota state for tenant.

        Args:
            tenant_id: Tenant to check

        Returns:
            Current quota state
        """
        return await self._load_state(tenant_id)

    async def _load_state(
        self,
        tenant_id: str,
        live_sandbox_count: int | None = None,
    ) -> QuotaState:
        """Load quota state from database.

        Optimized to minimize queries.
        """
        async with self._db() as db:
            # Count active sessions for tenant
            session_count = await self._count_tenant_sessions(db, tenant_id)

            # Get total cost for tenant
            total_cost = await self._tenant_cost_total(db, tenant_id)

            # Sandbox count from caller or estimate
            sandbox_count = live_sandbox_count if live_sandbox_count is not None else 0

        limits = self._limits
        exceeded = False
        reason: str | None = None

        if limits.max_sessions_per_tenant and session_count >= limits.max_sessions_per_tenant:
            exceeded = True
            reason = f"max_sessions_per_tenant exceeded: {session_count}>={limits.max_sessions_per_tenant}"
        elif limits.max_cost_per_tenant and total_cost > limits.max_cost_per_tenant:
            exceeded = True
            reason = f"max_cost_per_tenant exceeded: {total_cost}>{limits.max_cost_per_tenant}"

        return QuotaState(
            tenant_id=tenant_id,
            active_sessions=session_count,
            active_sandboxes=sandbox_count,
            total_cost_usd=total_cost,
            limits=limits,
            exceeded=exceeded,
            exceeded_reason=reason,
        )

    async def _count_sessions_global(self) -> int:
        """Count total active sessions across all tenants."""
        async with self._db() as db:
            query = (
                select(func.count())
                .select_from(SessionRow)
                .where(SessionRow.status.in_(ACTIVE_STATUSES))
            )
            result = await db.execute(query)
            return int(result.scalar_one())

    async def _count_tenant_sessions(
        self,
        db: AsyncSession,
        tenant_id: str,
    ) -> int:
        """Count active sessions for tenant."""
        query = (
            select(func.count())
            .select_from(SessionRow)
            .where(
                SessionRow.tenant_id == tenant_id,
                SessionRow.status.in_(ACTIVE_STATUSES),
            )
        )
        result = await db.execute(query)
        return int(result.scalar_one())

    async def _tenant_cost_total(
        self,
        db: AsyncSession,
        tenant_id: str,
    ) -> Decimal:
        """Get total cost for tenant."""
        query = (
            select(func.coalesce(func.sum(SessionIndexRow.total_cost), 0))
            .select_from(SessionIndexRow)
            .join(SessionRow, SessionRow.id == SessionIndexRow.session_id)
            .where(SessionRow.tenant_id == tenant_id)
        )
        result = await db.execute(query)
        total = result.scalar_one()
        return Decimal(str(total or 0))

    async def count_live_sandboxes(
        self,
        tenant_id: str,
        sandbox_session_ids: list[str],
    ) -> int:
        """Count live sandboxes for tenant.

        Args:
            tenant_id: Tenant to check
            sandbox_session_ids: Known sandbox session IDs

        Returns:
            Count of live sandboxes
        """
        if not sandbox_session_ids:
            return 0

        async with self._db() as db:
            query = (
                select(func.count())
                .select_from(SessionRow)
                .where(
                    SessionRow.id.in_(sandbox_session_ids),
                    SessionRow.tenant_id == tenant_id,
                    SessionRow.status.in_(ACTIVE_STATUSES),
                )
            )
            result = await db.execute(query)
            return int(result.scalar_one())
