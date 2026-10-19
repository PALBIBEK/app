"""Price every call, and stamp chat completions with the cost fields the supervisor reads.

Cost precedence: the upstream's own figure (OpenRouter reports one), else the
route's per-token prices (the ones it advertises on ``/model_group/info``), else a
conservative estimate. Cache reads and writes default to the input price, as QM
assumes when a gateway omits them.
"""

from __future__ import annotations

from typing import Any

from model_gateway.routes import Route
from model_gateway.wire_codec import TokenUsage

_PROMPT_USD_PER_TOKEN = 3.0 / 1_000_000
_COMPLETION_USD_PER_TOKEN = 15.0 / 1_000_000
_MIN_INVENTED_COST = 0.0001
"""Floor for unpriced calls so budgets still move."""


def estimate_cost_usd(*, prompt_tokens: int, completion_tokens: int) -> float:
    raw = (
        max(0, prompt_tokens) * _PROMPT_USD_PER_TOKEN
        + max(0, completion_tokens) * _COMPLETION_USD_PER_TOKEN
    )
    if raw <= 0:
        return _MIN_INVENTED_COST
    return round(raw, 8)


def cost_usd(usage: TokenUsage, route: Route | None) -> float:
    if usage.upstream_cost_usd is not None:
        return usage.upstream_cost_usd
    if route is None or not route.priced:
        return estimate_cost_usd(prompt_tokens=usage.prompt_tokens, completion_tokens=usage.output)
    input_price = route.input_cost_per_token or 0.0
    cache_read = route.cache_read_input_token_cost
    cache_write = route.cache_creation_input_token_cost
    total = (
        usage.input * input_price
        + usage.output * (route.output_cost_per_token or 0.0)
        + usage.cache_read * (input_price if cache_read is None else cache_read)
        + usage.cache_write * (input_price if cache_write is None else cache_write)
    )
    return round(total, 10)


def normalize_usage(
    raw: dict[str, Any] | None,
    *,
    invent_cost: bool = True,
    cost: float | None = None,
) -> dict[str, Any]:
    """Chat ``usage`` with prompt/completion and input/output tokens mirrored, plus ``cost``.

    ``cost`` (when given) wins; else the upstream ``cost_total`` / ``cost``; else,
    when ``invent_cost``, an estimate from tokens.
    """
    src = raw if isinstance(raw, dict) else {}
    prompt = int(src.get("prompt_tokens") or src.get("input_tokens") or 0)
    completion = int(src.get("completion_tokens") or src.get("output_tokens") or 0)
    total = int(src.get("total_tokens") or (prompt + completion))
    if cost is None:
        upstream = src.get("cost_total", src.get("cost"))
        if upstream is not None:
            cost = float(upstream)
        elif invent_cost:
            cost = estimate_cost_usd(prompt_tokens=prompt, completion_tokens=completion)
        else:
            cost = 0.0
    return {
        **src,
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": total,
        "input_tokens": prompt,
        "output_tokens": completion,
        "cost": cost,
        "cost_total": cost,
    }


def usage_cost_usd(usage: dict[str, Any] | None) -> float:
    if not isinstance(usage, dict):
        return 0.0
    return float(usage.get("cost_total") or usage.get("cost") or 0.0)
