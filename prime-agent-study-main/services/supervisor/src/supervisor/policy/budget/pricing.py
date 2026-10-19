"""Model pricing catalog — cost calculation for LLM API calls.

Provides pricing information for various models:
- Input cost per million tokens
- Output cost per million tokens
- Caching-related pricing

Prices are in USD and use Decimal for precision.

Reference: Anthropic/OpenAI/etc. pricing pages.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Final


__all__ = ["ModelPrice", "ModelPricing", "DEFAULT_PRICING"]


@dataclass(frozen=True, slots=True)
class ModelPrice:
    """Pricing for a specific model.

    Attributes:
        model_id: Model identifier (e.g., "claude-3-opus-20240229")
        input_cost_per_mtok: Cost per million input tokens in USD
        output_cost_per_mtok: Cost per million output tokens in USD
        cached_input_cost_per_mtok: Cost per million cached input tokens (optional)
    """

    model_id: str
    input_cost_per_mtok: Decimal
    output_cost_per_mtok: Decimal
    cached_input_cost_per_mtok: Decimal | None = None

    def calculate_cost(
        self,
        input_tokens: int,
        output_tokens: int,
        cached_input_tokens: int = 0,
    ) -> Decimal:
        """Calculate total cost for a request.

        Args:
            input_tokens: Number of input tokens
            output_tokens: Number of output tokens
            cached_input_tokens: Number of cached input tokens

        Returns:
            Total cost in USD
        """
        # Convert to millions
        input_mtok = Decimal(input_tokens) / Decimal("1000000")
        output_mtok = Decimal(output_tokens) / Decimal("1000000")
        cached_mtok = Decimal(cached_input_tokens) / Decimal("1000000")

        # Calculate costs
        input_cost = input_mtok * self.input_cost_per_mtok
        output_cost = output_mtok * self.output_cost_per_mtok

        # Cached input cost (use reduced rate or 0 if not specified)
        cached_cost = Decimal("0")
        if cached_input_tokens > 0 and self.cached_input_cost_per_mtok is not None:
            cached_cost = cached_mtok * self.cached_input_cost_per_mtok

        return input_cost + output_cost + cached_cost


class ModelPricing:
    """Catalog of model pricing.

    Provides pricing lookup by model ID with fallback to default.

    Example:
        pricing = ModelPricing()
        price = pricing.get_price("claude-3-opus-20240229")
        cost = price.calculate_cost(
            input_tokens=1000,
            output_tokens=500,
        )
    """

    __slots__ = ("_prices", "_default")

    def __init__(
        self,
        *,
        default_input_cost: Decimal = Decimal("0.015"),
        default_output_cost: Decimal = Decimal("0.075"),
    ) -> None:
        self._prices: dict[str, ModelPrice] = {}
        self._default = ModelPrice(
            model_id="default",
            input_cost_per_mtok=default_input_cost,
            output_cost_per_mtok=default_output_cost,
        )

    def register(self, price: ModelPrice) -> None:
        """Register pricing for a model.

        Args:
            price: Model pricing to register
        """
        self._prices[price.model_id] = price
        # Also register without version suffix for prefix matching
        if "-" in price.model_id:
            base = "-".join(price.model_id.split("-")[:-1])
            if base not in self._prices:
                self._prices[base] = price

    def get_price(self, model: str) -> ModelPrice:
        """Get pricing for model.

        Falls back to default if model not found.

        Args:
            model: Model identifier

        Returns:
            ModelPrice for the model
        """
        # Exact match
        if model in self._prices:
            return self._prices[model]

        # Prefix match (for versioned models)
        for model_id, price in self._prices.items():
            if model.startswith(model_id):
                return price

        return self._default

    def calculate_cost(
        self,
        model: str,
        input_tokens: int,
        output_tokens: int,
        cached_input_tokens: int = 0,
    ) -> Decimal:
        """Calculate cost for model usage.

        Args:
            model: Model identifier
            input_tokens: Number of input tokens
            output_tokens: Number of output tokens
            cached_input_tokens: Number of cached input tokens

        Returns:
            Total cost in USD
        """
        price = self.get_price(model)
        return price.calculate_cost(input_tokens, output_tokens, cached_input_tokens)


# Default pricing catalog (2024 prices)
DEFAULT_PRICING: Final[ModelPricing] = ModelPricing()

# Claude models
DEFAULT_PRICING.register(ModelPrice(
    model_id="claude-3-opus",
    input_cost_per_mtok=Decimal("15.00"),
    output_cost_per_mtok=Decimal("75.00"),
    cached_input_cost_per_mtok=Decimal("1.50"),
))
DEFAULT_PRICING.register(ModelPrice(
    model_id="claude-3-5-sonnet",
    input_cost_per_mtok=Decimal("3.00"),
    output_cost_per_mtok=Decimal("15.00"),
    cached_input_cost_per_mtok=Decimal("0.30"),
))
DEFAULT_PRICING.register(ModelPrice(
    model_id="claude-3-sonnet",
    input_cost_per_mtok=Decimal("3.00"),
    output_cost_per_mtok=Decimal("15.00"),
    cached_input_cost_per_mtok=Decimal("0.30"),
))
DEFAULT_PRICING.register(ModelPrice(
    model_id="claude-3-haiku",
    input_cost_per_mtok=Decimal("0.25"),
    output_cost_per_mtok=Decimal("1.25"),
    cached_input_cost_per_mtok=Decimal("0.03"),
))
DEFAULT_PRICING.register(ModelPrice(
    model_id="claude-3-5-haiku",
    input_cost_per_mtok=Decimal("1.00"),
    output_cost_per_mtok=Decimal("5.00"),
    cached_input_cost_per_mtok=Decimal("0.10"),
))

# OpenAI models
DEFAULT_PRICING.register(ModelPrice(
    model_id="gpt-4-turbo",
    input_cost_per_mtok=Decimal("10.00"),
    output_cost_per_mtok=Decimal("30.00"),
))
DEFAULT_PRICING.register(ModelPrice(
    model_id="gpt-4o",
    input_cost_per_mtok=Decimal("5.00"),
    output_cost_per_mtok=Decimal("15.00"),
))
DEFAULT_PRICING.register(ModelPrice(
    model_id="gpt-4o-mini",
    input_cost_per_mtok=Decimal("0.15"),
    output_cost_per_mtok=Decimal("0.60"),
))
DEFAULT_PRICING.register(ModelPrice(
    model_id="gpt-3.5-turbo",
    input_cost_per_mtok=Decimal("0.50"),
    output_cost_per_mtok=Decimal("1.50"),
))
