"""The native model registry: QM's model list, built on pi-ai's model templates.

Most entries clone a template — same protocol, endpoint and quirks — with their
own name, limits and prices. A *request override* makes an entry a selection of
another model rather than a model of its own: the Ultrafast entries call their
base model with ``service_tier: "ultrafast"`` at their own (6×) price card.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from supervisor.models.spec import (
    Compat,
    InputKind,
    ModelSpec,
    PriceTier,
    Pricing,
    RequestOverride,
    WireApi,
)

DEFAULT_AGENT_MODEL_ID = "claude-opus-5"

_ANTHROPIC = "https://api.anthropic.com"
_OPENAI = "https://api.openai.com/v1"
_TEXT_IMAGE: frozenset[InputKind] = frozenset({"text", "image"})
_ADAPTIVE = Compat(force_adaptive_thinking=True, supports_strict_tools=True)
_ADAPTIVE_NO_TEMPERATURE = Compat(
    force_adaptive_thinking=True, supports_strict_tools=True, supports_temperature=False
)


def _anthropic(
    id: str,
    name: str,
    rates: tuple[float, float, float, float],
    *,
    context_window: int = 1_000_000,
    max_tokens: int = 128_000,
    thinking: Mapping[str, str | None] | None = None,
    compat: Compat = _ADAPTIVE,
) -> ModelSpec:
    return ModelSpec(
        id=id,
        name=name,
        provider="anthropic",
        api=WireApi.ANTHROPIC_MESSAGES,
        base_url=_ANTHROPIC,
        reasoning=True,
        input=_TEXT_IMAGE,
        context_window=context_window,
        max_tokens=max_tokens,
        pricing=Pricing(*rates),
        thinking_level_map=dict(thinking or {}),
        compat=compat,
    )


TEMPLATES: Mapping[str, ModelSpec] = {
    spec.id: spec
    for spec in (
        _anthropic(
            "claude-opus-4-8",
            "Claude Opus 4.8",
            (5, 25, 0.5, 6.25),
            thinking={"xhigh": "xhigh", "max": "max"},
            compat=_ADAPTIVE_NO_TEMPERATURE,
        ),
        _anthropic(
            "claude-opus-4-7",
            "Claude Opus 4.7",
            (5, 25, 0.5, 6.25),
            thinking={"xhigh": "xhigh", "max": "max"},
            compat=_ADAPTIVE_NO_TEMPERATURE,
        ),
        _anthropic(
            "claude-opus-4-6", "Claude Opus 4.6", (5, 25, 0.5, 6.25), thinking={"max": "max"}
        ),
        _anthropic(
            "claude-fable-5",
            "Claude Fable 5",
            (10, 50, 1, 12.5),
            thinking={"off": None, "xhigh": "xhigh", "max": "max"},
        ),
        _anthropic(
            "claude-sonnet-5",
            "Claude Sonnet 5",
            (2, 10, 0.2, 2.5),
            thinking={"xhigh": "xhigh", "max": "max"},
        ),
        _anthropic(
            "claude-haiku-4-5",
            "Claude Haiku 4.5",
            (1, 5, 0.1, 1.25),
            context_window=200_000,
            max_tokens=64_000,
            compat=Compat(supports_strict_tools=True),
        ),
        ModelSpec(
            id="gpt-5.5",
            name="GPT-5.5",
            provider="openai",
            api=WireApi.OPENAI_RESPONSES,
            base_url=_OPENAI,
            reasoning=True,
            input=_TEXT_IMAGE,
            context_window=272_000,
            max_tokens=128_000,
            pricing=Pricing(5, 30, 0.5, 0, (PriceTier(10, 45, 1, 0, input_tokens_above=272_000),)),
            thinking_level_map={
                "off": "none",
                "minimal": None,
                "low": "low",
                "medium": "medium",
                "high": "high",
                "xhigh": "xhigh",
                "max": None,
            },
            compat=Compat(supports_strict_mode=True),
        ),
        ModelSpec(
            id="openrouter/auto",
            name="Auto Router",
            provider="openrouter",
            api=WireApi.OPENAI_COMPLETIONS,
            base_url="https://openrouter.ai/api/v1",
            reasoning=True,
            input=_TEXT_IMAGE,
            context_window=2_000_000,
            max_tokens=4_096,
            pricing=Pricing(0, 0, 0, 0),
            compat=Compat(supports_developer_role=False, thinking_format="openrouter"),
        ),
    )
}
"""pi-ai's built-in definitions for the models QM's registry clones or offers as-is."""


@dataclass(frozen=True, slots=True)
class Clone:
    template: str
    input: float
    output: float
    context_window: int
    max_tokens: int
    cache_read: float | None = None
    """Defaults to a tenth of ``input``."""
    cache_write: float = 0.0
    thinking_level_map: Mapping[str, str | None] | None = None
    tiers: tuple[PriceTier, ...] = ()


@dataclass(frozen=True, slots=True)
class ModelEntry:
    id: str
    name: str
    fast_mode: bool
    webui: bool
    """Offered in the model picker by default."""
    base: bool
    """Selectable as a session's base model."""
    button_label: str | None = None
    request: RequestOverride | None = None
    clone: Clone | None = None


_GPT_56_THINKING: Mapping[str, str | None] = {"max": "max"}
_GPT_6_THINKING: Mapping[str, str | None] = {"max": "max", "off": None}
_GPT_61_THINKING: Mapping[str, str | None] = {"off": None, "minimal": None, "max": "max"}


def _gpt56(
    input: float,
    output: float,
    *,
    cache_write: float,
    thinking: Mapping[str, str | None],
    long_context: tuple[float, float, float, float],
    cache_read: float | None = None,
) -> Clone:
    """A clone of GPT-5.5 at the 5.6+ window, with a long-context tier above 272k tokens."""
    return Clone(
        "gpt-5.5",
        input,
        output,
        context_window=1_050_000,
        max_tokens=128_000,
        cache_read=cache_read,
        cache_write=cache_write,
        thinking_level_map=thinking,
        tiers=(PriceTier(*long_context, input_tokens_above=272_000),),
    )


MODEL_REGISTRY: tuple[ModelEntry, ...] = (
    ModelEntry(
        "claude-opus-5-5",
        "Claude Opus 5.5",
        fast_mode=True,
        webui=True,
        base=True,
        clone=Clone(
            "claude-opus-4-8",
            4,
            20,
            1_000_000,
            128_000,
            cache_read=0.2,
            cache_write=5,
            thinking_level_map={"off": None},
        ),
    ),
    ModelEntry(
        "claude-fable-5-1",
        "Claude Fable 5.1",
        fast_mode=False,
        webui=True,
        base=True,
        clone=Clone(
            "claude-fable-5", 10, 50, 1_000_000, 128_000, cache_read=0.25, cache_write=12.5
        ),
    ),
    ModelEntry("claude-fable-5", "Claude Fable 5", fast_mode=False, webui=True, base=True),
    ModelEntry(
        "claude-opus-5",
        "Claude Opus 5",
        fast_mode=True,
        webui=True,
        base=True,
        clone=Clone("claude-opus-4-8", 5, 25, 1_000_000, 128_000, cache_write=6.25),
    ),
    ModelEntry("claude-opus-4-8", "Claude Opus 4.8", fast_mode=True, webui=True, base=True),
    ModelEntry(
        "claude-sonnet-5-5",
        "Claude Sonnet 5.5",
        fast_mode=False,
        webui=True,
        base=True,
        clone=Clone(
            "claude-opus-4-8",
            2,
            10,
            1_000_000,
            128_000,
            cache_read=0.1,
            cache_write=2.5,
            thinking_level_map={"off": None},
        ),
    ),
    ModelEntry("claude-sonnet-5", "Claude Sonnet 5", fast_mode=False, webui=True, base=True),
    ModelEntry(
        "claude-haiku-5-5",
        "Claude Haiku 5.5",
        fast_mode=False,
        webui=True,
        base=True,
        clone=Clone(
            "claude-opus-4-8",
            0.1,
            0.5,
            1_000_000,
            128_000,
            cache_read=0.01,
            cache_write=0.125,
            tiers=(PriceTier(0.5, 2.5, 0.05, 0.625, input_tokens_above=100_000),),
        ),
    ),
    ModelEntry("claude-haiku-4-5", "Claude Haiku 4.5", fast_mode=False, webui=True, base=True),
    ModelEntry(
        "gpt-5.6-sol",
        "GPT-5.6 Sol",
        fast_mode=True,
        webui=True,
        base=True,
        button_label="5.6 Sol",
        clone=_gpt56(
            4,
            20,
            cache_write=5,
            thinking=_GPT_56_THINKING,
            long_context=(8, 30, 0.8, 10),
        ),
    ),
    ModelEntry(
        "gpt-5.6-terra",
        "GPT-5.6 Terra",
        fast_mode=True,
        webui=True,
        base=True,
        button_label="5.6 Terra",
        clone=_gpt56(
            2,
            12,
            cache_write=2.5,
            thinking=_GPT_56_THINKING,
            long_context=(4, 18, 0.4, 5),
        ),
    ),
    ModelEntry(
        "gpt-5.6-luna",
        "GPT-5.6 Luna",
        fast_mode=True,
        webui=True,
        base=True,
        button_label="5.6 Luna",
        clone=_gpt56(
            0.2,
            1.2,
            cache_write=0.25,
            thinking=_GPT_56_THINKING,
            long_context=(0.4, 1.8, 0.04, 0.5),
        ),
    ),
    ModelEntry(
        "gpt-6-astra",
        "GPT-6 Astra",
        fast_mode=True,
        webui=True,
        base=True,
        button_label="Astra",
        clone=_gpt56(
            10,
            50,
            cache_write=12.5,
            cache_read=1,
            thinking=_GPT_6_THINKING,
            long_context=(20, 75, 2, 25),
        ),
    ),
    ModelEntry(
        "gpt-6-astra-ultrafast",
        "GPT-6 Astra · Ultrafast (6× cost)",
        fast_mode=False,
        webui=True,
        base=True,
        button_label="Astra Ultrafast",
        request=RequestOverride(model="gpt-6-astra", service_tier="ultrafast"),
        clone=_gpt56(
            60,
            300,
            cache_write=75,
            cache_read=6,
            thinking=_GPT_6_THINKING,
            long_context=(120, 450, 12, 150),
        ),
    ),
    ModelEntry(
        "gpt-6.1-sol",
        "GPT-6.1 Sol",
        fast_mode=True,
        webui=True,
        base=True,
        button_label="6.1 Sol",
        clone=_gpt56(
            2,
            10,
            cache_write=2.5,
            cache_read=0.1,
            thinking=_GPT_61_THINKING,
            long_context=(4, 15, 0.2, 5),
        ),
    ),
    ModelEntry(
        "gpt-6.1-sol-ultrafast",
        "GPT-6.1 Sol · Ultrafast (6× cost)",
        fast_mode=False,
        webui=True,
        base=True,
        button_label="6.1 Sol Ultrafast",
        request=RequestOverride(model="gpt-6.1-sol", service_tier="ultrafast"),
        clone=_gpt56(
            12,
            60,
            cache_write=15,
            cache_read=0.6,
            thinking=_GPT_61_THINKING,
            long_context=(24, 90, 1.2, 30),
        ),
    ),
    ModelEntry(
        "gpt-6-sol",
        "GPT-6 Sol",
        fast_mode=True,
        webui=True,
        base=True,
        button_label="6 Sol",
        clone=_gpt56(
            2,
            10,
            cache_write=2.5,
            thinking=_GPT_56_THINKING,
            long_context=(4, 15, 0.4, 5),
        ),
    ),
    ModelEntry(
        "gpt-6-luna",
        "GPT-6 Luna",
        fast_mode=True,
        webui=True,
        base=True,
        button_label="6 Luna",
        clone=_gpt56(
            0.1,
            0.5,
            cache_write=0.125,
            thinking=_GPT_56_THINKING,
            long_context=(0.2, 0.75, 0.02, 0.25),
        ),
    ),
    ModelEntry("openrouter/auto", "OpenRouter Auto", fast_mode=False, webui=True, base=True),
    ModelEntry("claude-opus-4-7", "Claude Opus 4.7", fast_mode=False, webui=False, base=False),
    ModelEntry("claude-opus-4-6", "Claude Opus 4.6", fast_mode=False, webui=False, base=False),
)

REGISTRY_BY_ID: Mapping[str, ModelEntry] = {entry.id: entry for entry in MODEL_REGISTRY}


def _cloned(entry: ModelEntry, clone: Clone) -> ModelSpec | None:
    template = TEMPLATES.get(clone.template)
    if template is None:
        return None
    return template.evolve(
        id=entry.id,
        name=entry.name,
        context_window=clone.context_window,
        max_tokens=clone.max_tokens,
        thinking_level_map={**template.thinking_level_map, **(clone.thinking_level_map or {})},
        pricing=Pricing(
            clone.input,
            clone.output,
            clone.input / 10 if clone.cache_read is None else clone.cache_read,
            clone.cache_write,
            clone.tiers,
        ),
    )


def resolve_builtin(model_id: str) -> ModelSpec | None:
    """The native spec for ``model_id``: a registry clone, else a template offered as-is."""
    entry = REGISTRY_BY_ID.get(model_id)
    if entry is not None and entry.clone is not None:
        return _cloned(entry, entry.clone)
    template = TEMPLATES.get(model_id)
    if template is not None and entry is not None:
        return template.evolve(name=entry.name)
    return template


def request_override(model_id: str) -> RequestOverride | None:
    entry = REGISTRY_BY_ID.get(model_id)
    return entry.request if entry is not None else None


def offered_in_picker(model_id: str) -> bool:
    entry = REGISTRY_BY_ID.get(model_id)
    return entry is not None and entry.webui and entry.base


