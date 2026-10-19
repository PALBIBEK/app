"""Gateway configuration and routing: which models go through the gateway, and how.

``MODEL_GATEWAY_URL``, ``MODEL_GATEWAY_API_KEY`` and ``MODEL_GATEWAY_API_KEY_HEADER``
route model calls through an authenticated LiteLLM-compatible gateway; the key
never leaves the supervisor. ``MODEL_GATEWAY_MODELS`` optionally maps native model
ids to gateway model groups (``claude-opus-5-5=claude-opus``) so the native
registry's definitions can be served through the gateway.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from typing import Protocol
from urllib.parse import urlsplit

from supervisor.models.spec import ModelSpec, WireApi

GATEWAY_MODEL_PREFIX = "gateway/"
GATEWAY_PROVIDER = "yal:gateway"

_HEADER_NAME = re.compile(r"^[!#$%&'*+.^_`|~0-9A-Za-z-]+$")
_ENV_NAMES = (
    "MODEL_GATEWAY_URL",
    "MODEL_GATEWAY_API_KEY",
    "MODEL_GATEWAY_API_KEY_HEADER",
    "MODEL_GATEWAY_MODELS",
)


class GatewayConfigError(ValueError):
    """The gateway environment is set but unusable; the process must not start."""


def is_gateway_model_id(model_id: str) -> bool:
    return model_id.startswith(GATEWAY_MODEL_PREFIX)


def parse_provider_base_url(env_name: str, value: str) -> str:
    """Normalize a base URL, rejecting anything that would silently misroute requests:
    non-HTTP(S) schemes, embedded credentials, query strings and fragments."""
    trimmed = value.strip().rstrip("/")
    try:
        parts = urlsplit(trimmed)
        _ = parts.port
    except ValueError:
        raise GatewayConfigError(f"{env_name} is not a valid URL: {value}") from None
    if not parts.scheme or not parts.netloc:
        raise GatewayConfigError(f"{env_name} is not a valid URL: {value}")
    if parts.scheme not in ("http", "https"):
        raise GatewayConfigError(f"{env_name} must be an http(s) URL, got {parts.scheme}://")
    if parts.username is not None or parts.password is not None:
        raise GatewayConfigError(f"{env_name} must not contain credentials")
    if parts.query:
        raise GatewayConfigError(f"{env_name} must not contain a query string")
    if parts.fragment:
        raise GatewayConfigError(f"{env_name} must not contain a fragment")
    return trimmed


def _parse_aliases(raw: str) -> dict[str, str]:
    aliases: dict[str, str] = {}
    for mapping in raw.split(",") if raw.strip() else []:
        source, separator, target = mapping.partition("=")
        source, target = source.strip(), target.strip()
        if not separator or not source or not target:
            raise GatewayConfigError(f"invalid MODEL_GATEWAY_MODELS mapping: {mapping}")
        if source in aliases:
            raise GatewayConfigError(f"duplicate MODEL_GATEWAY_MODELS source: {source}")
        aliases[source] = target
    return aliases


@dataclass(frozen=True, slots=True)
class GatewayConfig:
    url: str
    """Gateway base URL, with or without a trailing ``/v1``."""
    api_key: str
    api_key_header: str
    aliases: Mapping[str, str] = field(default_factory=dict)
    """Native model id → gateway model group."""

    @property
    def root(self) -> str:
        return self.url.removesuffix("/v1")

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> GatewayConfig | None:
        """The configured gateway, ``None`` when no gateway variable is set.

        Any gateway variable makes the URL, key and key header required.
        """
        if not any(env.get(name, "").strip() for name in _ENV_NAMES):
            return None
        for name in _ENV_NAMES[:3]:
            if not env.get(name, "").strip():
                raise GatewayConfigError(
                    f"{name} is required when model gateway routing is configured"
                )
        header = env["MODEL_GATEWAY_API_KEY_HEADER"].strip()
        if not _HEADER_NAME.fullmatch(header):
            raise GatewayConfigError(
                "MODEL_GATEWAY_API_KEY_HEADER must be a valid HTTP header name"
            )
        return cls(
            url=parse_provider_base_url("MODEL_GATEWAY_URL", env["MODEL_GATEWAY_URL"]),
            api_key=env["MODEL_GATEWAY_API_KEY"],
            api_key_header=header,
            aliases=_parse_aliases(env.get("MODEL_GATEWAY_MODELS", "")),
        )


class GatewayRoutes(Protocol):
    """The live routing view of a gateway (its discovery catalog keeps it current)."""

    @property
    def config(self) -> GatewayConfig: ...

    @property
    def models(self) -> Mapping[str, str]:
        """Routable model id → gateway model group, as of the last refresh."""
        ...

    @property
    def reserved_model_ids(self) -> frozenset[str]:
        """Configured alias sources: never served directly, even while unroutable."""
        ...


class GatewayModelUnavailable(Exception):
    """The gateway has no route for a model it owns; typed so callers never match its text."""

    status = 404
    type = "model_unavailable"
    code = "model_unavailable"
    retryable = False

    def __init__(self, model_id: str) -> None:
        super().__init__(f"Gateway model is unavailable: {model_id}")
        self.model_id = model_id


@dataclass(frozen=True, slots=True)
class GatewayRequest:
    """How to call one model through the gateway."""

    spec: ModelSpec
    """The model with its endpoint pointed at the gateway and session affinity on."""
    target: str
    """The gateway model group named on the wire."""
    api_key: str
    headers: Mapping[str, str]


def gateway_request(
    routes: GatewayRoutes | None, spec: ModelSpec, *, api_key: str | None = None
) -> GatewayRequest | None:
    """Route ``spec`` through the gateway, or ``None`` when the gateway does not serve it.

    A gateway model, or a native model configured as an alias, that the gateway
    does not currently route is unavailable — never silently served elsewhere.
    ``api_key`` overrides the configured key (a session's own virtual key).
    """
    target = routes.models.get(spec.id) if routes is not None else None
    if routes is None or target is None:
        if is_gateway_model_id(spec.id) or (
            routes is not None and spec.id in routes.reserved_model_ids
        ):
            raise GatewayModelUnavailable(spec.id)
        return None
    config = routes.config
    key = api_key or config.api_key
    if is_gateway_model_id(spec.id):
        base_url = config.root if spec.api is WireApi.ANTHROPIC_MESSAGES else f"{config.root}/v1"
    else:
        base_url = config.url
    return GatewayRequest(
        spec=spec.evolve(
            base_url=base_url,
            compat=replace(spec.compat, send_session_affinity_headers=True),
        ),
        target=target,
        api_key=key,
        headers={config.api_key_header: key},
    )
