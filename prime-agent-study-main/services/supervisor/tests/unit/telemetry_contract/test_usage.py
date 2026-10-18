"""RED → GREEN: Usage → GenAI token nesting (Phase1 §3.7)."""

from __future__ import annotations

from supervisor.agent.events import Usage
from supervisor.telemetry_contract.usage import to_genai_tokens, to_yal_cost


def test_zero_cache_passthrough() -> None:
    u = Usage(
        input_tokens=100,
        output_tokens=20,
        cache_read=0,
        cache_write=0,
        cost_total=0.01,
    )
    attrs = to_genai_tokens(u)
    assert attrs["gen_ai.usage.input_tokens"] == 100
    assert attrs["gen_ai.usage.output_tokens"] == 20
    assert attrs["gen_ai.usage.cache_read.input_tokens"] == 0
    assert attrs["gen_ai.usage.cache_creation.input_tokens"] == 0


def test_disjoint_counts_are_summed_for_input() -> None:
    """Fixture shape: input=10, cache_write=1801 — counts are disjoint."""
    u = Usage(
        input_tokens=10,
        output_tokens=96,
        cache_read=0,
        cache_write=1801,
        cost_total=0.00822375,
    )
    attrs = to_genai_tokens(u)
    assert attrs["gen_ai.usage.input_tokens"] == 10 + 0 + 1801
    assert attrs["gen_ai.usage.cache_creation.input_tokens"] == 1801
    assert attrs["gen_ai.usage.cache_read.input_tokens"] == 0


def test_cache_read_heavy_summed() -> None:
    u = Usage(
        input_tokens=50,
        output_tokens=10,
        cache_read=2000,
        cache_write=100,
        cost_total=0.05,
    )
    attrs = to_genai_tokens(u)
    assert attrs["gen_ai.usage.input_tokens"] == 50 + 2000 + 100
    assert attrs["gen_ai.usage.cache_read.input_tokens"] == 2000
    assert attrs["gen_ai.usage.cache_creation.input_tokens"] == 100


def test_yal_cost_includes_currency() -> None:
    u = Usage(
        input_tokens=1,
        output_tokens=1,
        cache_read=0,
        cache_write=0,
        cost_total=0.42,
    )
    cost = to_yal_cost(u, currency="USD")
    assert cost["yal.cost.total"] == 0.42
    assert cost["yal.cost.currency"] == "USD"
