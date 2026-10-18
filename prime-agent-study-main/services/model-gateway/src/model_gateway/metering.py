"""Normalize OpenAI-compat usage and attach supervisor-readable cost fields.

Supervisor/githi reads ``usage.cost_total`` or ``usage.cost`` (see githi loop).
Upstream providers may omit cost; we estimate from tokens when needed.
Echo invents a tiny positive cost so budgets move in tests/CI.
"""

from __future__ import annotations

from typing import Any

# Rough USD / token when upstream omits cost (OpenAI-ish mid-tier ballpark).
_PROMPT_USD_PER_TOKEN = 3.0 / 1_000_000
_COMPLETION_USD_PER_TOKEN = 15.0 / 1_000_000
# Floor so echo / tiny replies still meter non-zero for budget tests.
_MIN_INVENTED_COST = 0.0001


def estimate_cost_usd(*, prompt_tokens: int, completion_tokens: int) -> float:
    raw = (
        max(0, prompt_tokens) * _PROMPT_USD_PER_TOKEN
        + max(0, completion_tokens) * _COMPLETION_USD_PER_TOKEN
    )
    if raw <= 0:
        return _MIN_INVENTED_COST
    return round(raw, 8)


def normalize_usage(
    raw: dict[str, Any] | None,
    *,
    invent_cost: bool = True,
) -> dict[str, Any]:
    """Return usage with prompt/completion tokens plus ``cost`` and ``cost_total``.

    Mapping:
    - ``prompt_tokens`` / ``input_tokens`` → both mirrored
    - ``completion_tokens`` / ``output_tokens`` → both mirrored
    - ``cost`` / ``cost_total`` → same float (prefer upstream ``cost_total``, then ``cost``)
    - If cost missing and ``invent_cost``: pricing estimate from tokens (min floor)
    """
    src = raw if isinstance(raw, dict) else {}
    prompt = int(src.get("prompt_tokens") or src.get("input_tokens") or 0)
    completion = int(src.get("completion_tokens") or src.get("output_tokens") or 0)
    total = int(src.get("total_tokens") or (prompt + completion))

    cost_val = src.get("cost_total")
    if cost_val is None:
        cost_val = src.get("cost")
    if cost_val is None and invent_cost:
        cost = estimate_cost_usd(prompt_tokens=prompt, completion_tokens=completion)
    else:
        cost = float(cost_val or 0.0)

    out = dict(src)
    out["prompt_tokens"] = prompt
    out["completion_tokens"] = completion
    out["total_tokens"] = total
    out["input_tokens"] = prompt
    out["output_tokens"] = completion
    out["cost"] = cost
    out["cost_total"] = cost
    return out


def usage_cost_usd(usage: dict[str, Any] | None) -> float:
    if not isinstance(usage, dict):
        return 0.0
    return float(usage.get("cost_total") or usage.get("cost") or 0.0)


def stamp_completion_usage(result: dict[str, Any], *, invent_cost: bool = True) -> dict[str, Any]:
    """Copy ``result`` with normalized ``usage`` (no-op for stream marker payloads)."""
    if not isinstance(result, dict) or result.get("stream"):
        return result
    usage = normalize_usage(result.get("usage"), invent_cost=invent_cost)
    return {**result, "usage": usage}
