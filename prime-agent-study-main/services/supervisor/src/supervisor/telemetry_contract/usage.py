"""Usage → GenAI token attrs.

YAL ``Usage.input_tokens`` is DISJOINT from cache_read/cache_write (see harness
translate.py). Exported ``gen_ai.usage.input_tokens`` MUST include cache counts
per GenAI semconv nesting. Journal payload numbers and span numbers will differ
— that is intentional.
"""

from __future__ import annotations

from typing import Protocol


class UsageLike(Protocol):
    input_tokens: int
    output_tokens: int
    cache_read: int
    cache_write: int
    cost_total: float


def to_genai_tokens(u: UsageLike) -> dict[str, int]:
    """Sole place that performs nesting arithmetic for the span path."""
    return {
        "gen_ai.usage.input_tokens": u.input_tokens + u.cache_read + u.cache_write,
        "gen_ai.usage.output_tokens": u.output_tokens,
        "gen_ai.usage.cache_read.input_tokens": u.cache_read,
        "gen_ai.usage.cache_creation.input_tokens": u.cache_write,
    }


def to_yal_cost(u: UsageLike, *, currency: str = "USD") -> dict[str, float | str]:
    return {
        "yal.cost.total": u.cost_total,
        "yal.cost.currency": currency,
    }
