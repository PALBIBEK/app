"""Cost estimator — pre-execution cost estimation for early rejection.

Enables rejecting requests before expensive model calls:
- Estimate cost based on input tokens and expected output
- Check if estimate would exceed budget
- Fail fast without burning API credits

Reference: QM's cost estimation pattern.
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

from supervisor.policy.budget.pricing import DEFAULT_PRICING, ModelPricing

if TYPE_CHECKING:
    from supervisor.policy.budget.tracker import BudgetTracker


__all__ = ["CostEstimator"]


# Default estimated output ratio (output tokens as fraction of input)
DEFAULT_OUTPUT_RATIO = Decimal("0.5")

# Minimum estimated output tokens
MIN_ESTIMATED_OUTPUT = 100


class CostEstimator:
    """Estimates cost before execution for early rejection.

    Use this to:
    - Reject requests that would definitely exceed budget
    - Provide cost estimates to users
    - Implement "dry run" cost checks

    Example:
        estimator = CostEstimator(budget_tracker)

        # Before model call
        estimated_cost = estimator.estimate_turn_cost(
            model="claude-3-opus",
            input_tokens=50_000,
            estimated_output_tokens=10_000,
        )

        would_exceed = await estimator.would_exceed_budget(
            session_id="sess_123",
            estimated_cost=estimated_cost,
        )
        if would_exceed:
            raise BudgetExceeded("Estimated cost would exceed budget")
    """

    __slots__ = ("_pricing", "_tracker")

    def __init__(
        self,
        tracker: BudgetTracker,
        *,
        pricing: ModelPricing | None = None,
    ) -> None:
        self._tracker = tracker
        self._pricing = pricing or DEFAULT_PRICING

    def estimate_turn_cost(
        self,
        model: str,
        input_tokens: int,
        estimated_output_tokens: int | None = None,
    ) -> Decimal:
        """Estimate cost for a turn.

        Args:
            model: Model identifier
            input_tokens: Number of input tokens
            estimated_output_tokens: Expected output tokens (auto-estimated if None)

        Returns:
            Estimated cost in USD
        """
        if estimated_output_tokens is None:
            # Estimate output as ratio of input
            estimated = int(Decimal(input_tokens) * DEFAULT_OUTPUT_RATIO)
            estimated_output_tokens = max(estimated, MIN_ESTIMATED_OUTPUT)

        return self._pricing.calculate_cost(
            model=model,
            input_tokens=input_tokens,
            output_tokens=estimated_output_tokens,
        )

    async def would_exceed_budget(
        self,
        session_id: str,
        estimated_cost: Decimal,
        *,
        margin: Decimal = Decimal(0),
    ) -> bool:
        """Check if estimated cost would exceed budget.

        Args:
            session_id: Session to check
            estimated_cost: Estimated cost to add
            margin: Safety margin (added to estimate)

        Returns:
            True if would exceed budget
        """
        state = await self._tracker.check(session_id)

        # No cost limit configured
        if state.max_cost_usd is None:
            return False

        # Already exceeded
        if state.exceeded:
            return True

        # Check if estimate would exceed
        projected_cost = state.total_cost_usd + estimated_cost + margin
        return projected_cost > state.max_cost_usd

    async def remaining_budget(self, session_id: str) -> Decimal | None:
        """Get remaining budget for session.

        Args:
            session_id: Session to check

        Returns:
            Remaining budget in USD, or None if no limit
        """
        state = await self._tracker.check(session_id)

        if state.max_cost_usd is None:
            return None

        remaining = state.max_cost_usd - state.total_cost_usd
        return max(Decimal(0), remaining)

    async def estimate_with_check(
        self,
        session_id: str,
        model: str,
        input_tokens: int,
        estimated_output_tokens: int | None = None,
    ) -> tuple[Decimal, bool]:
        """Estimate cost and check budget in one call.

        Args:
            session_id: Session to check
            model: Model identifier
            input_tokens: Number of input tokens
            estimated_output_tokens: Expected output tokens

        Returns:
            Tuple of (estimated_cost, would_exceed)
        """
        estimated_cost = self.estimate_turn_cost(
            model=model,
            input_tokens=input_tokens,
            estimated_output_tokens=estimated_output_tokens,
        )

        would_exceed = await self.would_exceed_budget(
            session_id=session_id,
            estimated_cost=estimated_cost,
        )

        return estimated_cost, would_exceed
