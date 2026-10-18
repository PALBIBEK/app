"""Token accounting and cost.

Cost is what this harness is being optimised for, so it is measured from the
provider's own usage numbers rather than estimated afterwards. Cache tokens
stay separate throughout: they bill at roughly a tenth of fresh input, so
folding them in would both misreport spend and destroy the only evidence that
caching is doing anything.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..types import Usage

# Per million tokens, USD. Only models this project actually runs; anything
# else reports "unknown" rather than guessing, because a confidently wrong
# cost figure is worse than an absent one.
_PRICING: dict[str, tuple[float, float, float, float]] = {
    # model substring -> (input, output, cache_read, cache_write)
    "claude-haiku-4.5": (1.0, 5.0, 0.1, 1.25),
    "claude-3-haiku": (0.25, 1.25, 0.03, 0.30),
    "claude-sonnet-4.5": (3.0, 15.0, 0.30, 3.75),
    "claude-opus-4": (15.0, 75.0, 1.50, 18.75),
    "gpt-4o-mini": (0.15, 0.60, 0.075, 0.0),
    "gpt-4o": (2.50, 10.0, 1.25, 0.0),
}


@dataclass
class ModelPricing:
    input_per_m: float
    output_per_m: float
    cache_read_per_m: float = 0.0
    cache_write_per_m: float = 0.0

    @classmethod
    def for_model(cls, model: str) -> ModelPricing | None:
        name = model.lower()
        for key, (i, o, r, w) in _PRICING.items():
            if key in name:
                return cls(i, o, r, w)
        return None

    def cost(self, usage: Usage) -> float:
        return (
            usage.input_tokens * self.input_per_m
            + usage.output_tokens * self.output_per_m
            + usage.cache_read_tokens * self.cache_read_per_m
            + usage.cache_write_tokens * self.cache_write_per_m
        ) / 1_000_000


def parse_usage(raw: dict[str, Any] | None) -> Usage:
    """Normalise the two usage dialects into one shape.

    OpenAI reports cached tokens *inside* `prompt_tokens`; Anthropic reports
    them alongside it. Treating both the same way would double-count on one
    provider, so the OpenAI figure is subtracted back out.
    """
    if not raw:
        return Usage()

    prompt = int(raw.get("prompt_tokens") or 0)
    completion = int(raw.get("completion_tokens") or 0)

    details = raw.get("prompt_tokens_details") or {}
    openai_cached = int(details.get("cached_tokens") or 0)

    anthropic_read = int(raw.get("cache_read_input_tokens") or 0)
    anthropic_write = int(raw.get("cache_creation_input_tokens") or 0)

    cache_read = openai_cached or anthropic_read
    fresh_input = prompt - openai_cached if openai_cached else prompt

    return Usage(input_tokens=max(0, fresh_input), output_tokens=completion,
                 cache_read_tokens=cache_read, cache_write_tokens=anthropic_write)


@dataclass
class UsageTracker:
    total: Usage = field(default_factory=Usage)
    turns: int = 0

    def add(self, usage: Usage) -> None:
        self.total = self.total + usage
        self.turns += 1

    @property
    def cache_hit_rate(self) -> float:
        """Share of prompt tokens served from cache -- the single number that
        says whether caching is actually working."""
        sent = self.total.input_tokens + self.total.cache_read_tokens
        return self.total.cache_read_tokens / sent if sent else 0.0

    def summary(self, model: str) -> str:
        pricing = ModelPricing.for_model(model)
        parts = [
            f"{self.turns} turns",
            f"{self.total.total_tokens:,} tokens "
            f"(in {self.total.input_tokens:,}, out {self.total.output_tokens:,}, "
            f"cached {self.total.cache_read_tokens:,})",
            f"cache hit rate {self.cache_hit_rate:.0%}",
        ]
        if pricing:
            parts.append(f"cost ${pricing.cost(self.total):.4f}")
        else:
            parts.append("cost unknown (no pricing for this model)")
        return " | ".join(parts)
