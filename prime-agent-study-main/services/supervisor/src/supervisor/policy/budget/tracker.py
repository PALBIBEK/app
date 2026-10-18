"""Budget tracker — cost and token budget enforcement.

The session's running cost lives in ``session_index.total_cost`` and is only
changed by an atomic ``total_cost + :cost`` update, so it stays correct when a
session moves between workers or two of them record at once. Money is Decimal.
Tokens per turn are counted in the recording worker's memory.
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from supervisor.db.models import SessionIndexRow
from supervisor.policy.types import BudgetSpec, BudgetState

if TYPE_CHECKING:
    from supervisor.policy.protocols import AuditLoggerProtocol


__all__ = ["BudgetTracker"]


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
            max_cost_per_session=Decimal("10.00"),
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
        "_db",
        "_audit",
        "_session_limits",
        "_tokens_this_turn",
    )

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        audit: AuditLoggerProtocol | None = None,
    ) -> None:
        self._db = session_factory
        self._audit = audit
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

    async def check(self, session_id: str) -> BudgetState:
        """Check current budget state before turn.

        Args:
            session_id: Session to check

        Returns:
            Current budget state (``exceeded`` set; the caller decides to refuse)
        """
        cost = await self._load_cost(session_id)
        tokens = self._tokens_this_turn.get(session_id, 0)
        return self._evaluate(session_id, cost=cost, tokens=tokens)

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
        cost = (
            await self._add_cost(session_id, cost_usd)
            if cost_usd
            else await self._load_cost(session_id)
        )
        new_tokens = self._tokens_this_turn.get(session_id, 0) + tokens
        self._tokens_this_turn[session_id] = new_tokens
        return self._evaluate(session_id, cost=cost, tokens=new_tokens)

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
    ) -> BudgetState:
        """Evaluate budget state against limits."""
        spec = self._session_limits.get(session_id)
        max_cost = spec.max_cost_per_session if spec else None
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

        return BudgetState(
            session_id=session_id,
            total_cost_usd=cost,
            tokens_this_turn=tokens,
            max_cost_usd=max_cost,
            max_tokens_per_turn=max_tokens,
            exceeded=exceeded,
            exceeded_reason=reason,
        )

    async def _load_cost(self, session_id: str) -> Decimal:
        async with self._db() as db:
            total = await db.scalar(
                select(SessionIndexRow.total_cost).where(SessionIndexRow.session_id == session_id)
            )
        return Decimal(str(total or 0))

    async def _add_cost(self, session_id: str, cost: Decimal) -> Decimal:
        """Add ``cost`` to the session total in one statement; returns the new total."""
        add = (
            update(SessionIndexRow)
            .where(SessionIndexRow.session_id == session_id)
            .values(total_cost=SessionIndexRow.total_cost + cost)
            .returning(SessionIndexRow.total_cost)
        )
        async with self._db() as db:
            total = await db.scalar(add)
            if total is None:
                # No index row yet; a concurrent first insert loses and adds instead.
                try:
                    db.add(SessionIndexRow(session_id=session_id, total_cost=cost))
                    await db.commit()
                    return cost
                except IntegrityError:
                    await db.rollback()
                    total = await db.scalar(add)
            await db.commit()
        return Decimal(str(total))
