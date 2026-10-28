"""What a model is: its wire protocol, limits, prices, reasoning controls and quirks.

A :class:`ModelSpec` is everything a transport needs to talk to one model, and
everything the session needs to price and size a conversation for it. Specs come
from two places — the native registry (:mod:`supervisor.models.registry`) and the
gateway's discovery catalog (:mod:`supervisor.models.catalog`) — and are
otherwise indistinguishable.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Literal, Self

PER_MILLION = 1_000_000


class WireApi(StrEnum):
    """The request/response protocol a model is spoken to in."""

    ANTHROPIC_MESSAGES = "anthropic-messages"
    OPENAI_RESPONSES = "openai-responses"
    OPENAI_COMPLETIONS = "openai-completions"


type ThinkingLevel = Literal["off", "minimal", "low", "medium", "high", "xhigh", "max"]
type InputKind = Literal["text", "image"]

THINKING_LEVELS: tuple[ThinkingLevel, ...] = (
    "off",
    "minimal",
    "low",
    "medium",
    "high",
    "xhigh",
    "max",
)


@dataclass(frozen=True, slots=True)
class TokenUsage:
    """Tokens one call consumed; ``input`` excludes cache reads and writes."""

    input: int = 0
    output: int = 0
    cache_read: int = 0
    cache_write: int = 0
    cache_write_1h: int = 0
    """Of ``cache_write``, the tokens written to the one-hour cache (billed at 2× input)."""
    reasoning: int = 0
    """Of ``output``, the tokens spent reasoning."""

    @property
    def prompt_tokens(self) -> int:
        return self.input + self.cache_read + self.cache_write

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.output


@dataclass(frozen=True, slots=True)
class Cost:
    """USD for one call, by component."""

    input: float = 0.0
    output: float = 0.0
    cache_read: float = 0.0
    cache_write: float = 0.0

    @property
    def total(self) -> float:
        return self.input + self.output + self.cache_read + self.cache_write

    def scaled(self, factor: float) -> Cost:
        return Cost(
            self.input * factor,
            self.output * factor,
            self.cache_read * factor,
            self.cache_write * factor,
        )


@dataclass(frozen=True, slots=True)
class Rates:
    """USD per million tokens."""

    input: float
    output: float
    cache_read: float
    cache_write: float


@dataclass(frozen=True, slots=True)
class PriceTier(Rates):
    """Rates that replace the base rates once a call's prompt exceeds ``input_tokens_above``."""

    input_tokens_above: int = 0


@dataclass(frozen=True, slots=True)
class Pricing(Rates):
    tiers: tuple[PriceTier, ...] = ()

    def rates_for(self, prompt_tokens: int) -> Rates:
        """The highest tier the prompt crosses, else the base rates."""
        rates: Rates = self
        threshold = -1
        for tier in self.tiers:
            if prompt_tokens > tier.input_tokens_above > threshold:
                rates, threshold = tier, tier.input_tokens_above
        return rates

    def cost(self, usage: TokenUsage) -> Cost:
        rates = self.rates_for(usage.prompt_tokens)
        short_write = usage.cache_write - usage.cache_write_1h
        return Cost(
            input=rates.input * usage.input / PER_MILLION,
            output=rates.output * usage.output / PER_MILLION,
            cache_read=rates.cache_read * usage.cache_read / PER_MILLION,
            cache_write=(rates.cache_write * short_write + rates.input * 2 * usage.cache_write_1h)
            / PER_MILLION,
        )


@dataclass(frozen=True, slots=True)
class Compat:
    """Per-model protocol quirks; ``None`` means "use the transport's default"."""

    supports_developer_role: bool | None = None
    supports_store: bool | None = None
    supports_strict_mode: bool | None = None
    supports_strict_tools: bool | None = None
    supports_temperature: bool | None = None
    supports_reasoning_effort: bool | None = None
    supports_usage_in_streaming: bool | None = None
    supports_long_cache_retention: bool | None = None
    supports_eager_tool_input_streaming: bool | None = None
    supports_cache_control_on_tools: bool | None = None
    force_adaptive_thinking: bool = False
    send_session_affinity_headers: bool | None = None
    allow_empty_signature: bool = False
    max_tokens_field: Literal["max_tokens", "max_completion_tokens"] | None = None
    thinking_format: Literal["openai", "openrouter"] | None = None
    session_affinity_format: Literal["openai", "openrouter"] | None = None
    cache_control_format: Literal["anthropic"] | None = None


@dataclass(frozen=True, slots=True)
class RequestOverride:
    """Fields merged into every request body for a model (an Ultrafast base model and tier)."""

    model: str
    service_tier: str


@dataclass(frozen=True, slots=True)
class ModelSpec:
    id: str
    name: str
    provider: str
    api: WireApi
    base_url: str
    context_window: int
    max_tokens: int
    pricing: Pricing
    reasoning: bool = False
    input: frozenset[InputKind] = frozenset({"text"})
    thinking_level_map: Mapping[str, str | None] = field(default_factory=dict)
    compat: Compat = Compat()
    headers: Mapping[str, str] = field(default_factory=dict)
    document_input: Literal["pdf", "files"] | None = None

    def evolve(self, **changes: Any) -> Self:
        return dataclasses.replace(self, **changes)

    # -- reasoning ---------------------------------------------------------

    def supported_thinking_levels(self) -> tuple[ThinkingLevel, ...]:
        """Levels this model accepts: a level mapped to ``None`` is excluded, and the
        ``xhigh`` / ``max`` levels exist only when the model maps them explicitly."""
        if not self.reasoning:
            return ("off",)
        levels: list[ThinkingLevel] = []
        for level in THINKING_LEVELS:
            if level in self.thinking_level_map and self.thinking_level_map[level] is None:
                continue
            if level in ("xhigh", "max") and level not in self.thinking_level_map:
                continue
            levels.append(level)
        return tuple(levels)

    def clamp_thinking_level(self, level: ThinkingLevel) -> ThinkingLevel:
        """The nearest supported level: the next higher one, else the next lower one."""
        available = self.supported_thinking_levels()
        if level in available:
            return level
        index = THINKING_LEVELS.index(level)
        for candidate in (*THINKING_LEVELS[index:], *reversed(THINKING_LEVELS[:index])):
            if candidate in available:
                return candidate
        return available[0] if available else "off"

    @property
    def supports_adaptive_thinking(self) -> bool:
        return (
            self.api is WireApi.ANTHROPIC_MESSAGES
            and self.reasoning
            and self.compat.force_adaptive_thinking
        )

    # -- sizing ------------------------------------------------------------

    @property
    def context_token_budget(self) -> int | None:
        """How much conversation to keep: half the room left after the output cap, at most 150k."""
        if self.max_tokens <= 0 or self.max_tokens >= self.context_window:
            return None
        return min(CONTEXT_BUDGET_CAP_TOKENS, int((self.context_window - self.max_tokens) * 0.5))


CONTEXT_BUDGET_CAP_TOKENS = 150_000


def default_thinking_level(spec: ModelSpec) -> ThinkingLevel:
    """What an unspecified ("auto") effort means for a model: medium, pi's default for all."""
    del spec
    return "medium"
