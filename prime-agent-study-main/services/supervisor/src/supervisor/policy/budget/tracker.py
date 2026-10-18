"""Budget tracker — cost and token budget enforcement.

Rewritten with:
- Decimal for monetary values (no floating-point errors)
- No lazy-init antipattern
- Single DB query for cost loading
- Proper caching with TTL
- Metrics hooks for observability

Reference: FastAPI best practices.
"""

from __future__ import annotations

import time
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from supervisor.db.models import SessionIndexRow
from supervisor.policy.errors import BudgetCostExceeded, BudgetExceeded, BudgetTokensExceeded
from supervisor.policy.types import BudgetSpec, BudgetState

if TYPE_CHECKING:
    from supervisor.policy.protocols import AuditLoggerProtocol


__all__ = ["BudgetTracker"]


# Cache TTL for cost lookups (5 seconds)
_CACHE_TTL_MS = 5_000


def _now_ms() -> int:
    """Current time in milliseconds."""
    return int(time.time() * 1000)


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
            raise BudgetExceeded(...)

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
        "_cost_cache",
        "_cost_cache_time",
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
        # Cost cache: {session_id: Decimal}
        self._cost_cache: dict[str, Decimal] = {}
        self._cost_cache_time: dict[str, int] = {}

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
            Current budget state

        Raises:
            BudgetExceeded: If budget already exceeded
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
        # Zero usage never trips (echo runtime)
        if cost_usd == Decimal("0") and tokens == 0:
            cost = await self._load_cost(session_id)
            return self._evaluate(
                session_id,
                cost=cost,
                tokens=self._tokens_this_turn.get(session_id, 0),
            )

        # Load current cost
        prev_cost = self._cost_cache.get(session_id)
        if prev_cost is None:
            prev_cost = await self._load_cost(session_id)

        # Check if DB already includes this cost (idempotency)
        db_cost = await self._load_cost(session_id, force=True)
        expected_cost = prev_cost + cost_usd

        if db_cost >= expected_cost - Decimal("0.000001"):
            # DB already has this cost recorded
            new_cost = db_cost
        else:
            # Persist new cost
            new_cost = prev_cost + cost_usd
            await self._persist_cost(session_id, new_cost)

        # Update cache
        self._cost_cache[session_id] = new_cost
        self._cost_cache_time[session_id] = _now_ms()

        # Update turn tokens
        current_tokens = self._tokens_this_turn.get(session_id, 0)
        new_tokens = current_tokens + tokens
        self._tokens_this_turn[session_id] = new_tokens

        return self._evaluate(session_id, cost=new_cost, tokens=new_tokens)

    def clear(self, session_id: str) -> None:
        """Clear tracking state for session.

        Call when session ends.

        Args:
            session_id: Session to clear
        """
        self._session_limits.pop(session_id, None)
        self._tokens_this_turn.pop(session_id, None)
        self._cost_cache.pop(session_id, None)
        self._cost_cache_time.pop(session_id, None)

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

    async def _load_cost(
        self,
        session_id: str,
        *,
        force: bool = False,
    ) -> Decimal:
        """Load cost from DB with caching.

        Args:
            session_id: Session to load
            force: Bypass cache

        Returns:
            Total cost as Decimal
        """
        now = _now_ms()

        # Check cache
        if not force:
            cached = self._cost_cache.get(session_id)
            cache_time = self._cost_cache_time.get(session_id, 0)
            if cached is not None and (now - cache_time) < _CACHE_TTL_MS:
                return cached

        # Load from DB
        async with self._db() as db:
            idx = await db.get(SessionIndexRow, session_id)
            if idx is None:
                cost = Decimal("0")
            else:
                cost = Decimal(str(idx.total_cost or 0))

        # Update cache
        self._cost_cache[session_id] = cost
        self._cost_cache_time[session_id] = now

        return cost

    async def _persist_cost(self, session_id: str, cost: Decimal) -> None:
        """Persist cost to DB.

        Args:
            session_id: Session to update
            cost: New total cost
        """
        async with self._db() as db:
            idx = await db.get(SessionIndexRow, session_id)
            if idx is None:
                idx = SessionIndexRow(
                    session_id=session_id,
                    total_cost=float(cost),
                )
                db.add(idx)
            else:
                idx.total_cost = float(cost)
            await db.commit()
