"""Budget tracker — cost and token budget enforcement.

The session's running cost lives in ``session_index.total_cost`` and is only
changed by an atomic ``total_cost + :cost`` update, so it stays correct when a
session moves between workers or two of them record at once. Money is Decimal.
Tokens per turn are counted in the recording worker's memory.

Each charge also counts toward its tenant's spend this calendar month (UTC), in
``tenant_spend``, in the same transaction. With ``tenant_limit`` set
(``TENANT_MAX_COST_PER_TENANT``), a tenant past it is over budget in every session:
the checks before and after each model call stop the run, until the month turns or
the limit is raised. The tenant's spend is kept apart from its sessions, so erasing
one does not give its spend back.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from supervisor.db.models import SessionIndexRow, SessionRow, TenantSpendRow
from supervisor.harness.core.types import BudgetSpec
from supervisor.policy.types import BudgetState

if TYPE_CHECKING:
    from supervisor.policy.protocols import AuditLoggerProtocol


__all__ = ["BudgetTracker", "current_month", "tenant_spend"]


MAX_TREE_DEPTH = 16
"""Sessions above a subagent followed at most (harness depth caps are far smaller)."""


async def _with_ancestors(db: AsyncSession, session_id: str) -> list[str]:
    """``session_id`` and the sessions that started it (subagents), nearest first."""
    chain = [session_id]
    while len(chain) <= MAX_TREE_DEPTH and (
        parent := await db.scalar(select(SessionRow.parent_session_id).where(SessionRow.id == chain[-1]))
    ):
        chain.append(parent)
    return chain


async def _add_session_cost(db: AsyncSession, session_id: str, cost: Decimal) -> Decimal:
    add = (
        update(SessionIndexRow)
        .where(SessionIndexRow.session_id == session_id)
        .values(total_cost=SessionIndexRow.total_cost + cost)
        .returning(SessionIndexRow.total_cost)
    )
    total = await db.scalar(add)
    if total is None:
        # No index row yet; a concurrent first insert loses and adds instead.
        try:
            async with db.begin_nested():
                db.add(SessionIndexRow(session_id=session_id, total_cost=cost))
        except IntegrityError:
            total = await db.scalar(add)
    return cost if total is None else Decimal(str(total))


def current_month() -> str:
    """This calendar month in UTC, as ``tenant_spend`` keys it (``YYYY-MM``)."""
    return datetime.now(UTC).strftime("%Y-%m")


async def tenant_spend(db: AsyncSession, tenant_id: str, month: str) -> Decimal:
    """What ``tenant_id`` has spent in ``month``."""
    spent = await db.scalar(
        select(TenantSpendRow.cost_usd).where(TenantSpendRow.tenant_id == tenant_id, TenantSpendRow.month == month)
    )
    return Decimal(str(spent or 0))


class BudgetTracker:
    """Tracks and enforces budget limits per session.

    Design:
    - Uses Decimal for all monetary calculations
    - Caches cost with TTL to reduce DB queries
    - Per-turn token counter resets on begin_turn()
    - Thread-safe for concurrent sessions

    Example:
        tracker = BudgetTracker(session_factory)

        # Configure limits from manifest
        tracker.configure("sess_123", BudgetSpec(
            max_cost_per_session=10.0,
            max_tokens_per_turn=100_000,
        ))

        # Before each turn
        tracker.begin_turn("sess_123")
        state = await tracker.check("sess_123")
        if state.exceeded:
            raise BudgetExceeded.from_state(state)

        # After model response
        state = await tracker.record(
            "sess_123",
            cost_usd=Decimal("0.05"),
            tokens=1500,
        )
    """

    __slots__ = (
        "_audit",
        "_db",
        "_month",
        "_session_limits",
        "_tenant_limit",
        "_tokens_this_turn",
    )

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        audit: AuditLoggerProtocol | None = None,
        tenant_limit: Decimal | None = None,
        month: Callable[[], str] = current_month,
    ) -> None:
        """``tenant_limit``: the most a tenant may spend in a calendar month (None: no limit)."""
        self._db = session_factory
        self._audit = audit
        self._tenant_limit = tenant_limit
        self._month = month
        # Per-session configuration
        self._session_limits: dict[str, BudgetSpec | None] = {}
        # Per-turn token counter
        self._tokens_this_turn: dict[str, int] = {}

    def configure(self, session_id: str, spec: BudgetSpec | None) -> None:
        """Configure budget limits for a session.

        Args:
            session_id: Session to configure
            spec: Budget specification from manifest
        """
        self._session_limits[session_id] = spec

    def begin_turn(self, session_id: str) -> None:
        """Reset per-turn token counter.

        Call at the start of each turn.

        Args:
            session_id: Session starting new turn
        """
        self._tokens_this_turn[session_id] = 0

    async def check(self, session_id: str, *, turn_tokens: bool = True) -> BudgetState:
        """Check current budget state before turn.

        Args:
            session_id: Session to check
            turn_tokens: Also hold the turn to ``max_tokens_per_turn`` as this worker
                counted it; False where the workflow counts a run's tokens itself (a
                stepped run, whose model steps run on any worker)

        Returns:
            Current budget state (``exceeded`` set; the caller decides to refuse)
        """
        cost, tenant = await self._load_cost(session_id)
        tokens = self._tokens_this_turn.get(session_id, 0) if turn_tokens else 0
        return self._evaluate(session_id, cost=cost, tenant=tenant, tokens=tokens)

    async def record(
        self,
        session_id: str,
        cost_usd: Decimal,
        tokens: int,
    ) -> BudgetState:
        """Record usage and return updated state.

        Args:
            session_id: Session to record for
            cost_usd: Cost in USD
            tokens: Total tokens (input + output)

        Returns:
            Updated budget state
        """
        # The database holds the session total, never a worker's memory: a session
        # moves between workers, and two may record for it at once.
        cost, tenant = (
            await self._add_cost(session_id, cost_usd)
            if cost_usd
            else await self._load_cost(session_id)
        )
        new_tokens = self._tokens_this_turn.get(session_id, 0) + tokens
        self._tokens_this_turn[session_id] = new_tokens
        return self._evaluate(session_id, cost=cost, tenant=tenant, tokens=new_tokens)

    def clear(self, session_id: str) -> None:
        """Clear tracking state for session.

        Call when session ends.

        Args:
            session_id: Session to clear
        """
        self._session_limits.pop(session_id, None)
        self._tokens_this_turn.pop(session_id, None)

    def _evaluate(
        self,
        session_id: str,
        *,
        cost: Decimal,
        tokens: int,
        tenant: Decimal = Decimal(0),
    ) -> BudgetState:
        """Evaluate budget state against limits (``tenant``: the tenant's spend this month)."""
        spec = self._session_limits.get(session_id)
        # The manifest states USD as a float; money is compared as Decimal.
        max_cost = (
            Decimal(str(spec.max_cost_per_session))
            if spec and spec.max_cost_per_session is not None
            else None
        )
        max_tokens = spec.max_tokens_per_turn if spec else None

        exceeded = False
        reason: str | None = None

        # Check cost limit
        if max_cost is not None and cost > max_cost:
            exceeded = True
            reason = f"max_cost_per_session exceeded: cost={cost} limit={max_cost}"

        # Check token limit
        elif max_tokens is not None and tokens > max_tokens:
            exceeded = True
            reason = f"max_tokens_per_turn exceeded: tokens={tokens} limit={max_tokens}"

        # The tenant's spend this month, across all its sessions
        elif self._tenant_limit is not None and tenant >= self._tenant_limit:
            exceeded = True
            reason = (
                f"the tenant's model spend this month reached its limit: "
                f"spent={tenant} limit={self._tenant_limit} (it resets when the month turns)"
            )

        return BudgetState(
            session_id=session_id,
            total_cost_usd=cost,
            tokens_this_turn=tokens,
            max_cost_usd=max_cost,
            max_tokens_per_turn=max_tokens,
            exceeded=exceeded,
            exceeded_reason=reason,
        )

    async def _load_cost(self, session_id: str) -> tuple[Decimal, Decimal]:
        """What counts toward the session's cost cap (its tree's spend: a subagent shares its
        root's cap), and its tenant's spend this month (0 when none is limited)."""
        async with self._db() as db:
            root = await db.scalar(
                select(func.coalesce(SessionRow.root_session_id, SessionRow.id)).where(SessionRow.id == session_id)
            )
            total = await db.scalar(
                select(SessionIndexRow.total_cost).where(SessionIndexRow.session_id == (root or session_id))
            )
            tenant = await self._tenant_spend(db, session_id) if self._tenant_limit is not None else Decimal(0)
        return Decimal(str(total or 0)), tenant

    async def _add_cost(self, session_id: str, cost: Decimal) -> tuple[Decimal, Decimal]:
        """Add ``cost`` to the session's total, to every session above it (a subagent's spend
        is its parent's too, so each total is the session's own plus its subagents'), and to
        its tenant's month (once), in one transaction; returns the tree's new total (its
        root's, which the cap is held to) and the tenant's."""
        async with self._db() as db:
            total: Decimal | None = None
            for sid in await _with_ancestors(db, session_id):
                total = await _add_session_cost(db, sid, cost)
            tenant = await self._add_tenant_cost(db, session_id, cost)
            await db.commit()
        return (cost if total is None else total), tenant

    async def _add_tenant_cost(self, db: AsyncSession, session_id: str, cost: Decimal) -> Decimal:
        tenant_id = await db.scalar(select(SessionRow.tenant_id).where(SessionRow.id == session_id))
        if tenant_id is None:
            return Decimal(0)
        month = self._month()
        add = (
            update(TenantSpendRow)
            .where(TenantSpendRow.tenant_id == tenant_id, TenantSpendRow.month == month)
            .values(cost_usd=TenantSpendRow.cost_usd + cost)
            .returning(TenantSpendRow.cost_usd)
        )
        spent = await db.scalar(add)
        if spent is None:
            try:
                async with db.begin_nested():
                    db.add(TenantSpendRow(tenant_id=tenant_id, month=month, cost_usd=cost))
            except IntegrityError:  # another worker counted the month's first charge at once
                spent = await db.scalar(add)
        return cost if spent is None else Decimal(str(spent))

    async def _tenant_spend(self, db: AsyncSession, session_id: str) -> Decimal:
        tenant_id = await db.scalar(select(SessionRow.tenant_id).where(SessionRow.id == session_id))
        return Decimal(0) if tenant_id is None else await tenant_spend(db, tenant_id, self._month())
