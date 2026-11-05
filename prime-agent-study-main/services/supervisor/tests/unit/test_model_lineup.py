"""The vendors' current models, as the gateway offers them once a vendor has a key: with their
list prices and limits, a name a person reads, and reasoning they can always run with."""

from __future__ import annotations

import pytest

from supervisor.gateway.config import Settings
from supervisor.gateway.routes import VENDOR_ROUTES
from supervisor.models.catalog import admit
from supervisor.models.spec import WireApi
from supervisor.models.transports import RequestOptions, WireCall
from supervisor.models.transports.base import Context
from supervisor.models.transports.responses import build_request

LINEUP = {
    "anthropic": ["claude-sonnet-5", "claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5"],
    "openai": ["gpt-5.5", "gpt-6-astra", "gpt-6.1-sol", "gpt-6.1-terra", "gpt-6-luna"],
}


@pytest.mark.parametrize("vendor", sorted(LINEUP))
def test_a_vendors_key_unlocks_its_current_models(vendor: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MODEL_GATEWAY_ROUTES_JSON", raising=False)

    table = Settings().routes({vendor})

    assert set(LINEUP[vendor]) <= set(table)
    assert all(table[rid].provider == vendor for rid in LINEUP[vendor])


@pytest.mark.parametrize(
    ("route", "label", "per_million", "window"),
    [
        ("claude-opus-5-5", "Claude Opus 5.5", (4, 20, 0.2, 5), 1_000_000),
        ("claude-sonnet-5-5", "Claude Sonnet 5.5", (2, 10, 0.1, 2.5), 1_000_000),
        ("claude-haiku-5-5", "Claude Haiku 5.5", (0.1, 0.5, 0.01, 0.125), 100_000),
        ("gpt-6-astra", "GPT-6 Astra", (10, 50, 1, 12.5), 272_000),
        ("gpt-6.1-sol", "GPT-6.1 Sol", (2, 10, 0.1, 2.5), 272_000),
        ("gpt-6-luna", "GPT-6 Luna", (0.1, 0.5, 0.01, 0.125), 272_000),
    ],
)
def test_each_model_carries_its_list_price_and_the_window_that_price_holds_for(
    route: str, label: str, per_million: tuple[float, ...], window: int
) -> None:
    """Prices as the vendors list them (platform.claude.com pricing; developers.openai.com
    pricing): a route holds one price, so its window stops where the vendor's price changes."""
    found = {r: v for routes in VENDOR_ROUTES.values() for r, v in routes.items()}[route]
    prices = (found.input_cost_per_token, found.output_cost_per_token, found.cache_read_input_token_cost, found.cache_creation_input_token_cost)

    assert found.label == label
    assert tuple(round(p * 1e6, 6) for p in prices if p is not None) == per_million
    assert found.context_window == window and found.max_output_tokens is not None and found.max_output_tokens < window
    assert found.supports_vision and found.supports_reasoning


def _spec(route: str):
    found = {r: v for routes in VENDOR_ROUTES.values() for r, v in routes.items()}[route]
    entry = admit(found.model_group_info(route), frozenset({route}), "http://gateway/v1")
    assert entry is not None
    return entry


def test_the_picker_reads_each_models_name_not_its_id() -> None:
    assert (_spec("claude-opus-5-5").name, _spec("gpt-6.1-terra").name) == ("Claude Opus 5.5", "GPT-6.1 Terra")
    assert (_spec("claude-opus-5-5").api, _spec("gpt-6-astra").api) == (WireApi.ANTHROPIC_MESSAGES, WireApi.OPENAI_RESPONSES)


@pytest.mark.parametrize(("route", "effort"), [("gpt-6-astra", "low"), ("gpt-6.1-sol", "low"), ("gpt-6-luna", "none"), ("gpt-5.5", "none")])
def test_with_thinking_off_a_model_that_cannot_turn_reasoning_off_is_asked_for_its_lowest(route: str, effort: str) -> None:
    """GPT-6 Astra and 6.1 Sol refuse ``none`` (developers.openai.com/api/docs/guides/latest-model)."""
    call = WireCall(
        spec=_spec(route),
        context=Context(system_prompt="", messages=[], tools=[]),
        api_key="k",
        http=None,  # type: ignore[arg-type] — only the request is built
        headers={},
        options=RequestOptions(thinking="off"),
    )

    _, _, body = build_request(call)

    assert body["reasoning"] == {"effort": effort}
