"""Usage → GenAI token attrs.

PRIME ``Usage.input_tokens`` is DISJOINT from cache_read/cache_write (see harness
translate.py). Exported ``gen_ai.usage.input_tokens`` MUST include cache counts
per GenAI semconv nesting. Journal payload numbers and span numbers will differ
— that is intentional.
"""

from __future__ import annotations

from typing import Protocol


class UsageLike(Protocol):
    @property
    def input_tokens(self) -> int: ...
    @property
    def output_tokens(self) -> int: ...
    @property
    def cache_read(self) -> int: ...
    @property
    def cache_write(self) -> int: ...
    @property
    def cost_total(self) -> float: ...


def to_genai_tokens(u: UsageLike) -> dict[str, int]:
    """Sole place that performs nesting arithmetic for the span path."""
    return {
        "gen_ai.usage.input_tokens": u.input_tokens + u.cache_read + u.cache_write,
        "gen_ai.usage.output_tokens": u.output_tokens,
        "gen_ai.usage.cache_read.input_tokens": u.cache_read,
        "gen_ai.usage.cache_creation.input_tokens": u.cache_write,
    }


def to_prime_cost(u: UsageLike, *, currency: str = "USD") -> dict[str, float | str]:
    return {
        "prime.cost.total": u.cost_total,
        "prime.cost.currency": currency,
    }
