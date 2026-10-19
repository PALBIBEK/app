"""The native model registry, pricing and reasoning controls (ported from QM and pi-ai)."""

from __future__ import annotations

import pytest

from supervisor.models.registry import (
    DEFAULT_AGENT_MODEL_ID,
    MODEL_REGISTRY,
    auxiliary_model_for,
    offered_in_picker,
    request_override,
    resolve_builtin,
)
from supervisor.models.spec import PriceTier, Pricing, TokenUsage, WireApi, default_thinking_level


def _spec(model_id: str):
    spec = resolve_builtin(model_id)
    assert spec is not None, model_id
    return spec


def test_every_registry_entry_resolves() -> None:
    for entry in MODEL_REGISTRY:
        spec = _spec(entry.id)
        assert spec.id == entry.id
        assert spec.name == entry.name
    assert resolve_builtin(DEFAULT_AGENT_MODEL_ID) is not None
    assert resolve_builtin("no-such-model") is None


def test_a_clone_keeps_its_template_protocol_and_takes_its_own_card() -> None:
    opus = _spec("claude-opus-5-5")
    assert opus.api is WireApi.ANTHROPIC_MESSAGES
    assert opus.base_url == "https://api.anthropic.com"
    assert opus.compat.force_adaptive_thinking
    assert (
        opus.pricing.input,
        opus.pricing.output,
        opus.pricing.cache_read,
        opus.pricing.cache_write,
    ) == (
        4,
        20,
        0.2,
        5,
    )
    assert opus.thinking_level_map == {"xhigh": "xhigh", "max": "max", "off": None}

    defaulted = _spec("claude-opus-5")
    assert defaulted.pricing.cache_read == pytest.approx(0.5)  # a tenth of input when unset


@pytest.mark.parametrize(
    ("base_id", "short_cost", "long_cost"),
    [("gpt-6-astra", 0.3, 6.35), ("gpt-6.1-sol", 0.055, 1.26)],
)
def test_ultrafast_is_its_base_model_at_six_times_the_full_card(
    base_id: str, short_cost: float, long_cost: float
) -> None:
    ultrafast = _spec(f"{base_id}-ultrafast")
    standard = _spec(base_id)
    assert ultrafast.provider == "openai"
    assert ultrafast.api is WireApi.OPENAI_RESPONSES
    override = request_override(ultrafast.id)
    assert override is not None
    assert (override.model, override.service_tier) == (base_id, "ultrafast")
    assert request_override(base_id) is None
    for tokens, expected in (
        (TokenUsage(input=10_000, output=2_000, cache_read=50_000, cache_write=4_000), short_cost),
        (TokenUsage(input=300_000, output=2_000, cache_read=50_000, cache_write=4_000), long_cost),
    ):
        base_cost = standard.pricing.cost(tokens).total
        assert base_cost == pytest.approx(expected, abs=1e-9)
        assert ultrafast.pricing.cost(tokens).total == pytest.approx(base_cost * 6, abs=1e-9)


def test_the_highest_crossed_tier_prices_the_whole_call() -> None:
    pricing = Pricing(
        1,
        2,
        0.1,
        1.25,
        (
            PriceTier(10, 20, 1, 12.5, input_tokens_above=100),
            PriceTier(5, 10, 0.5, 6, input_tokens_above=50),
        ),
    )
    assert pricing.rates_for(50).input == 1
    assert pricing.rates_for(51).input == 5
    assert pricing.rates_for(101).input == 10


def test_one_hour_cache_writes_bill_twice_the_input_rate() -> None:
    pricing = Pricing(3, 15, 0.3, 3.75)
    cost = pricing.cost(TokenUsage(cache_write=1_000_000, cache_write_1h=400_000))
    assert cost.cache_write == pytest.approx(3.75 * 0.6 + 3 * 2 * 0.4)


def test_thinking_levels_follow_the_level_map() -> None:
    assert _spec("gpt-5.5").supported_thinking_levels() == ("off", "low", "medium", "high", "xhigh")
    sol = _spec("gpt-6.1-sol")
    assert sol.supported_thinking_levels() == ("low", "medium", "high", "xhigh", "max")
    assert sol.clamp_thinking_level("off") == "low"
    assert sol.clamp_thinking_level("minimal") == "low"
    haiku = _spec("claude-haiku-4-5")
    assert haiku.supported_thinking_levels() == ("off", "minimal", "low", "medium", "high")
    assert haiku.clamp_thinking_level("max") == "high"
    router = _spec("openrouter/auto")
    assert router.reasoning
    assert default_thinking_level(_spec("claude-opus-5")) == "low"
    assert default_thinking_level(_spec("gpt-5.6-sol")) == "medium"


def test_context_budget_is_half_the_room_after_output_capped_at_150k() -> None:
    assert _spec("claude-haiku-4-5").context_token_budget == (200_000 - 64_000) // 2
    assert _spec("claude-opus-5").context_token_budget == 150_000


def test_picker_and_auxiliary_models() -> None:
    assert offered_in_picker("claude-opus-5-5")
    assert not offered_in_picker("claude-opus-4-7")
    assert auxiliary_model_for("claude-opus-5-5") == "claude-haiku-5-5"
    assert auxiliary_model_for("gpt-6-astra") == "gpt-5.6-luna"
    assert auxiliary_model_for("unknown") == "unknown"
