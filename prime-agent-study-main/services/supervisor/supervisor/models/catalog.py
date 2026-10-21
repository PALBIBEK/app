"""Gateway model discovery: what the gateway serves, kept current.

The catalog reads the gateway's ``GET /v1/models`` and ``GET /model_group/info``
(LiteLLM's end-user metadata endpoint) with the gateway key, and admits a model
group only when both endpoints list it, it is a chat model with tool support, and
it carries valid context, output and pricing metadata. Admitted groups become
``gateway/<group>`` models whose wire protocol follows their provider labels —
never their names:

* only Anthropic providers → native Messages (signed thinking survives tool calls);
* only OpenAI or Azure providers → Responses (encrypted reasoning, native files);
* anything else → OpenAI-compatible chat completions.

Availability refreshes on demand every five minutes, and before inference when
expired. A successful refresh replaces the catalog, empty or not. After discovery
has once succeeded, a transient failure keeps the last catalog for up to fifteen
minutes; any other failure disables gateway routes until recovery. Failures retry
after thirty seconds. Configured aliases (``MODEL_GATEWAY_MODELS``) stay routable
before the first successful discovery and afterwards only while their target is
advertised.
"""

from __future__ import annotations

import json
import logging
import math
import re
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

import httpx

from supervisor.models.endpoints import (
    GATEWAY_MODEL_PREFIX,
    GATEWAY_PROVIDER,
    GatewayConfig,
)
from supervisor.models.registry import offered_in_picker
from supervisor.models.spec import Compat, ModelSpec, Pricing, WireApi

logger = logging.getLogger(__name__)

MAX_BYTES = 2 * 1024 * 1024
MAX_MODELS = 1_000
TTL_S = 5 * 60
RETRY_S = 30
STALE_S = 15 * 60
FETCH_TIMEOUT_S = 5.0

_MODEL_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/+-]{0,191}$")
_PDF_PROVIDERS = frozenset({"anthropic", "openai", "azure", "gemini", "vertex_ai"})


@dataclass(frozen=True, slots=True)
class EmbeddingModel:
    id: str
    name: str
    provider: str
    base_url: str


type GatewayEntry = ModelSpec | EmbeddingModel


class CatalogError(Exception):
    """A discovery read failed; ``transient`` failures keep a recent catalog alive."""

    def __init__(self, message: str, *, status: int | None = None, transient: bool = False) -> None:
        super().__init__(message)
        self.status = status
        self.transient = transient or status == 429 or (status is not None and status >= 500)


def _is_model_id(value: object) -> bool:
    return isinstance(value, str) and _MODEL_ID.fullmatch(value) is not None


_MAX_SAFE_INTEGER = 2**53 - 1


def _tokens(value: object) -> int | None:
    """A positive safe integer (JSON ``200000.0`` counts, as it does in JavaScript)."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    if isinstance(value, float) and not value.is_integer():
        return None
    count = int(value)
    return count if 0 < count <= _MAX_SAFE_INTEGER else None


def _per_million(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    scaled = float(value) * 1_000_000
    return scaled if math.isfinite(scaled) and value >= 0 else None


def admit(info: object, allowed: frozenset[str], base_url: str) -> GatewayEntry | None:
    """The model a ``/model_group/info`` row describes, or ``None`` when it is not offerable."""
    if not isinstance(info, dict):
        return None
    group = info.get("model_group")
    if not _is_model_id(group) or group not in allowed:
        return None
    assert isinstance(group, str)
    model_id = GATEWAY_MODEL_PREFIX + group
    if info.get("mode") == "embedding":
        return EmbeddingModel(id=model_id, name=group, provider=GATEWAY_PROVIDER, base_url=base_url)
    if info.get("mode") != "chat" or info.get("supports_function_calling") is not True:
        return None
    context_window = _tokens(info.get("max_input_tokens"))
    max_tokens = _tokens(info.get("max_output_tokens"))
    input_price = _per_million(info.get("input_cost_per_token"))
    output_price = _per_million(info.get("output_cost_per_token"))
    if (
        context_window is None
        or max_tokens is None
        or max_tokens >= context_window
        or input_price is None
        or output_price is None
    ):
        return None
    raw_providers = info.get("providers")
    providers = [str(p) for p in raw_providers] if isinstance(raw_providers, list) else []
    anthropic = bool(providers) and all(p == "anthropic" for p in providers)
    openai = bool(providers) and all(p in ("openai", "azure") for p in providers)
    pdf = bool(providers) and all(p in _PDF_PROVIDERS for p in providers)
    api = (
        WireApi.ANTHROPIC_MESSAGES
        if anthropic
        else WireApi.OPENAI_RESPONSES
        if openai
        else WireApi.OPENAI_COMPLETIONS
    )
    params = info.get("supported_openai_params")
    openai_params = {str(p) for p in params} if isinstance(params, list) else set()
    cache_read = _per_million(info.get("cache_read_input_token_cost"))
    cache_write = _per_million(info.get("cache_creation_input_token_cost"))
    return ModelSpec(
        id=model_id,
        name=group,
        provider=GATEWAY_PROVIDER,
        api=api,
        base_url=base_url.removesuffix("/v1") if anthropic else base_url,
        reasoning=(anthropic or openai) and info.get("supports_reasoning") is True,
        input=frozenset({"text", "image"})
        if info.get("supports_vision") is True
        else frozenset({"text"}),
        context_window=context_window,
        max_tokens=max_tokens,
        pricing=Pricing(
            input_price,
            output_price,
            input_price if cache_read is None else cache_read,
            input_price if cache_write is None else cache_write,
        ),
        compat=Compat(
            supports_developer_role=False,
            supports_store=False,
            supports_strict_mode=False,
            supports_strict_tools=False,
            force_adaptive_thinking=anthropic and info.get("supports_adaptive_thinking") is True,
            supports_long_cache_retention=False,
            supports_reasoning_effort="reasoning_effort" in openai_params,
            supports_usage_in_streaming=True,
            send_session_affinity_headers=True,
            max_tokens_field=(
                "max_completion_tokens"
                if "max_completion_tokens" in openai_params
                else "max_tokens"
            ),
        ),
        document_input="files" if openai else "pdf" if pdf else None,
    )


@dataclass(slots=True)
class GatewayModels:
    """The admitted models, versioned so readers can tell when the set changed."""

    _models: dict[str, GatewayEntry] = field(default_factory=dict)
    _aliased: frozenset[str] = frozenset()
    _snapshot: tuple[tuple[GatewayEntry, ...], tuple[str, ...]] = ((), ())
    version: int = 0

    def replace(self, models: list[GatewayEntry], aliased: list[str] | None = None) -> None:
        snapshot = (tuple(models), tuple(aliased or ()))
        if snapshot == self._snapshot:
            return
        self._models = {m.id: m for m in models}
        self._aliased = frozenset(snapshot[1])
        self._snapshot = snapshot
        self.version += 1

    def resolve(self, model_id: str) -> ModelSpec | None:
        model = self._models.get(model_id)
        return model if isinstance(model, ModelSpec) else None


    def chat_models(self, *, include_aliased: bool = False) -> list[ModelSpec]:
        """Discovered chat models; a group a native alias already offers is hidden by default."""
        return [
            m
            for m in self._models.values()
            if isinstance(m, ModelSpec) and (include_aliased or m.id not in self._aliased)
        ]

    def embedding_ids(self) -> list[str]:
        return [m.id for m in self._models.values() if isinstance(m, EmbeddingModel)]


class GatewayCatalog:
    """Discovery state for one gateway; also its live routing view (:class:`GatewayRoutes`)."""

    def __init__(
        self,
        config: GatewayConfig,
        *,
        transport: httpx.BaseTransport | None = None,
        now: Callable[[], float] = time.monotonic,
    ) -> None:
        self._config = config
        self._transport = transport
        self._now = now
        self._routes: Mapping[str, str] = dict(config.aliases)
        self._reserved = frozenset(config.aliases)
        self._discovered = False
        self._expires_at = 0.0
        self._retry_at = 0.0
        self._last_success_at = 0.0
        self._lock = threading.Lock()
        self.admitted = GatewayModels()
        """The admitted models (the routing table is :attr:`models`)."""

    # -- GatewayRoutes --------------------------------------------------------

    @property
    def config(self) -> GatewayConfig:
        return self._config

    @property
    def models(self) -> Mapping[str, str]:
        """Routable model id → gateway model group."""
        return self._routes

    @property
    def reserved_model_ids(self) -> frozenset[str]:
        return self._reserved

    # -- refresh --------------------------------------------------------------

    def refresh(self) -> None:
        """Re-read the gateway when due. One refresh runs at a time; callers that arrive
        during it wait for it and then share its result."""
        if self._now() < self._retry_at:
            return
        with self._lock:
            if self._now() < self._retry_at:
                return
            try:
                self._discover()
            except Exception as exc:  # noqa: BLE001 — every failure decides the catalog's fate below
                self._failed(exc)

    def _discover(self) -> None:
        root = self._config.root
        base_url = f"{root}/v1"
        listing = self._read(f"{base_url}/models")
        metadata = self._read(f"{root}/model_group/info")
        allowed: set[str] = set()
        for entry in listing:
            model_id = entry.get("id") if isinstance(entry, dict) else None
            if not _is_model_id(model_id):
                raise CatalogError("Invalid gateway model id")
            assert isinstance(model_id, str)
            allowed.add(model_id)
        frozen_allowed = frozenset(allowed)
        models = [
            m for m in (admit(row, frozen_allowed, base_url) for row in metadata) if m is not None
        ]
        routes: dict[str, str] = {m.id: m.id.removeprefix(GATEWAY_MODEL_PREFIX) for m in models}
        embeddings = {m.id for m in models if isinstance(m, EmbeddingModel)}
        for source, target in self._config.aliases.items():
            if target in frozen_allowed and GATEWAY_MODEL_PREFIX + target not in embeddings:
                routes[source] = target
        now = self._now()
        self._routes = routes
        self._discovered = True
        self._expires_at = now + TTL_S
        self._retry_at = self._expires_at
        self._last_success_at = now
        self.admitted.replace(
            models,
            [
                GATEWAY_MODEL_PREFIX + target
                for source, target in self._config.aliases.items()
                if not source.startswith(GATEWAY_MODEL_PREFIX)
                and offered_in_picker(source)
                and source in routes
            ],
        )

    def _failed(self, exc: Exception) -> None:
        now = self._now()
        self._retry_at = now + RETRY_S
        transient = isinstance(exc, CatalogError) and exc.transient
        if self._discovered and transient and now - self._last_success_at < STALE_S:
            logger.warning(
                "gateway model discovery failed; keeping last known models, retrying in 30 seconds"
            )
            return
        if self._discovered or not self._config.aliases:
            self._routes = {}
            self._expires_at = 0.0
            self.admitted.replace([])
            logger.warning("gateway model discovery unavailable; retrying in 30 seconds: %s", exc)

    def _read(self, url: str) -> list[Any]:
        """GET a catalog endpoint: keyed, 5 s, no redirects, at most 2 MiB and 1000 entries."""
        headers = {self._config.api_key_header: self._config.api_key}
        try:
            with (
                httpx.Client(
                    transport=self._transport, timeout=FETCH_TIMEOUT_S, follow_redirects=False
                ) as client,
                client.stream("GET", url, headers=headers) as response,
            ):
                if response.is_redirect:
                    raise CatalogError("Gateway catalog redirected", transient=True)
                if not response.is_success:
                    raise CatalogError(
                        f"Gateway catalog returned HTTP {response.status_code}",
                        status=response.status_code,
                    )
                length = response.headers.get("content-length")
                if length is not None and length.isdigit() and int(length) > MAX_BYTES:
                    raise CatalogError("Gateway catalog exceeds size limit")
                body = bytearray()
                for chunk in response.iter_bytes():
                    body += chunk
                    if len(body) > MAX_BYTES:
                        raise CatalogError("Gateway catalog exceeds size limit")
        except httpx.TransportError as exc:
            raise CatalogError(
                f"Gateway catalog unreachable: {type(exc).__name__}", transient=True
            ) from exc
        try:
            parsed = json.loads(bytes(body))
        except ValueError as exc:
            raise CatalogError("Gateway catalog is not JSON") from exc
        data = parsed.get("data") if isinstance(parsed, dict) else None
        if not isinstance(data, list) or len(data) > MAX_MODELS:
            raise CatalogError("Invalid gateway catalog")
        return data
