"""Route catalog: what each route id calls, and the model-group metadata it advertises.

A route is the gateway's unit of access control, metering and discovery. Besides
the upstream it calls, a route carries the metadata LiteLLM serves from
``GET /model_group/info`` — mode, provider labels, token limits, per-token prices
and capability flags — so a QM-style client can discover it, pick a wire protocol
from its providers, and price its usage without a hand-maintained model list.
"""

from __future__ import annotations

import json
from collections.abc import Iterator, Mapping
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    NonNegativeFloat,
    NonNegativeInt,
    PositiveInt,
    TypeAdapter,
    ValidationError,
)

type TokenPrice = NonNegativeFloat
"""USD per token, the unit LiteLLM uses on the wire."""

RouteId = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:/+-]{0,191}$")]
"""The model-id grammar QM accepts from discovery; anything else is never offered."""

_ROUTE_ID: TypeAdapter[str] = TypeAdapter(RouteId)


class Route(BaseModel):
    """One routable model group."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    provider: str
    """Registered provider that serves this route (``echo``, ``openai``, …)."""
    upstream_model: str
    mode: Literal["chat", "embedding"] = "chat"
    providers: tuple[str, ...] | None = None
    """LiteLLM provider labels; defaults to ``(provider,)``. Clients choose a wire from these."""
    context_window: PositiveInt | None = None
    """Input capacity in tokens, advertised as ``max_input_tokens``."""
    max_output_tokens: PositiveInt | None = None
    input_cost_per_token: TokenPrice | None = None
    output_cost_per_token: TokenPrice | None = None
    cache_read_input_token_cost: TokenPrice | None = None
    cache_creation_input_token_cost: TokenPrice | None = None
    supports_function_calling: bool = True
    supports_vision: bool = False
    supports_reasoning: bool = False
    supports_adaptive_thinking: bool = False
    supported_openai_params: tuple[str, ...] = ()
    retries: NonNegativeInt | None = None
    """Extra attempts on a transient upstream failure; ``None`` keeps the provider default."""
    fallback_route_ids: tuple[str, ...] = ()

    @property
    def provider_labels(self) -> tuple[str, ...]:
        return self.providers if self.providers is not None else (self.provider,)

    @property
    def priced(self) -> bool:
        return self.input_cost_per_token is not None and self.output_cost_per_token is not None

    def model_group_info(self, route_id: str) -> dict[str, Any]:
        """The ``/model_group/info`` row for this route (no upstream or credential fields)."""
        return {
            "model_group": route_id,
            "mode": self.mode,
            "providers": list(self.provider_labels),
            "max_input_tokens": self.context_window,
            "max_output_tokens": self.max_output_tokens,
            "input_cost_per_token": self.input_cost_per_token,
            "output_cost_per_token": self.output_cost_per_token,
            "cache_read_input_token_cost": self.cache_read_input_token_cost,
            "cache_creation_input_token_cost": self.cache_creation_input_token_cost,
            "supports_function_calling": self.supports_function_calling,
            "supports_vision": self.supports_vision,
            "supports_reasoning": self.supports_reasoning,
            "supports_adaptive_thinking": self.supports_adaptive_thinking,
            "supported_openai_params": list(self.supported_openai_params),
        }


def route_view(route_id: str, route: Route) -> dict[str, Any]:
    """A route as ``/v1/routes`` shows it: what it calls and how much it can hold."""
    return {
        "id": route_id,
        "provider": route.provider,
        "upstream_model": route.upstream_model,
        "context_window": route.context_window,
    }


class RouteConfigError(ValueError):
    """``MODEL_GATEWAY_ROUTES_JSON`` is not a valid route table."""


class RouteTable(Mapping[str, Route]):
    """Immutable route id → :class:`Route` map."""

    __slots__ = ("_routes",)

    def __init__(self, routes: Mapping[str, Route]) -> None:
        self._routes = dict(routes)

    @classmethod
    def parse(cls, raw: Mapping[str, Any]) -> RouteTable:
        if not raw:
            raise RouteConfigError("route table must be a non-empty object")
        routes: dict[str, Route] = {}
        for route_id, meta in raw.items():
            try:
                _ROUTE_ID.validate_python(route_id)
                routes[route_id] = meta if isinstance(meta, Route) else Route.model_validate(meta)
            except ValidationError as exc:
                first = exc.errors()[0]
                where = ".".join(str(part) for part in first["loc"]) or "id"
                raise RouteConfigError(f"route {route_id!r}: {where}: {first['msg']}") from None
        return cls(routes)

    @classmethod
    def from_json(cls, text: str) -> RouteTable:
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as exc:
            raise RouteConfigError(f"route table is not JSON: {exc.msg}") from None
        if not isinstance(parsed, dict):
            raise RouteConfigError("route table must be a JSON object")
        return cls.parse(parsed)

    def with_defaults(self, extra: Mapping[str, Route]) -> RouteTable:
        """Add ``extra`` routes whose ids are not already configured."""
        return RouteTable(
            {**self._routes, **{k: v for k, v in extra.items() if k not in self._routes}}
        )

    def __getitem__(self, route_id: str) -> Route:
        return self._routes[route_id]

    def __iter__(self) -> Iterator[str]:
        return iter(self._routes)

    def __len__(self) -> int:
        return len(self._routes)


_ECHO_PRICE = 0.0005
"""Echo bills a visible amount per token so budgets and metering move in dev and CI."""


def _echo(upstream: str, *providers: str) -> Route:
    return Route(
        provider="echo",
        upstream_model=upstream,
        providers=providers or None,
        context_window=200_000,
        max_output_tokens=8_192,
        input_cost_per_token=_ECHO_PRICE,
        output_cost_per_token=_ECHO_PRICE,
        supported_openai_params=("max_tokens", "tools", "tool_choice"),
    )


DEFAULT_ROUTES: Mapping[str, Route] = {
    "echo": _echo("echo"),
    "fake": _echo("fake"),
    "githi": Route(
        provider="openrouter",
        upstream_model="anthropic/claude-sonnet-4",
        context_window=200_000,
        max_output_tokens=64_000,
        input_cost_per_token=3e-6,
        output_cost_per_token=15e-6,
        cache_read_input_token_cost=0.3e-6,
        cache_creation_input_token_cost=3.75e-6,
        supports_vision=True,
        supported_openai_params=("max_tokens", "tools", "tool_choice", "temperature"),
    ),
}
"""Routes served when ``MODEL_GATEWAY_ROUTES_JSON`` is unset."""


VENDOR_ROUTES: Mapping[str, Mapping[str, Route]] = {
    "openrouter": {"githi": DEFAULT_ROUTES["githi"]},
    "openai": {
        "gpt-5.5": Route(
            provider="openai",
            upstream_model="gpt-5.5",
            context_window=272_000,
            max_output_tokens=128_000,
            input_cost_per_token=5e-6,
            output_cost_per_token=30e-6,
            cache_read_input_token_cost=0.5e-6,
            supports_vision=True,
            supports_reasoning=True,
            supported_openai_params=(
                "max_completion_tokens",
                "reasoning_effort",
                "tools",
                "tool_choice",
                "service_tier",
            ),
        ),
    },
    "anthropic": {
        "claude-sonnet-5": Route(
            provider="anthropic",
            upstream_model="claude-sonnet-5",
            context_window=1_000_000,
            max_output_tokens=128_000,
            input_cost_per_token=2e-6,
            output_cost_per_token=10e-6,
            cache_read_input_token_cost=0.2e-6,
            cache_creation_input_token_cost=2.5e-6,
            supports_vision=True,
            supports_reasoning=True,
            supports_adaptive_thinking=True,
            supported_openai_params=("max_tokens", "tools", "tool_choice", "reasoning_effort"),
        ),
    },
}
"""Routes a vendor credential unlocks when it is set but the route table omits them."""
