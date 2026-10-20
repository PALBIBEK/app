"""Model calls routed by model id: resolve the spec, route it, speak its wire protocol.

:class:`ModelRouter` is the process-wide half — the gateway's discovery catalog,
the native registry and the call audit. :class:`RoutedModelClient` is one
session's half: it implements the ``model_gateway`` seam (and ``ModelCatalog``),
calling with the session's own key and affinity id.

A model id resolves, in order, to:

1. ``gateway/<group>`` — a group the gateway advertises (discovered);
2. a native registry id — QM's model list, served through the gateway when
   ``MODEL_GATEWAY_MODELS`` aliases it;
3. a bare gateway group (a legacy route id) — as ``gateway/<group>``.

Keys live in the gateway: a model the gateway does not route has no other way out.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal
from urllib.parse import urlsplit

import httpx

from supervisor.models.audit import ModelCallAudit, ModelCallRecord
from supervisor.models.catalog import GatewayCatalog
from supervisor.models.endpoints import (
    GATEWAY_MODEL_PREFIX,
    GatewayConfig,
    GatewayModelUnavailable,
    GatewayRequest,
    gateway_request,
    is_gateway_model_id,
)
from supervisor.models.messages import (
    Context,
    TextBlock,
    ToolCallBlock,
    blocks_to_parts,
    context_from_chat,
    estimate_context_tokens,
)
from supervisor.models.registry import offered_in_picker, request_override, resolve_builtin
from supervisor.models.spec import ModelSpec, ThinkingLevel, default_thinking_level
from supervisor.models.transports import (
    TRANSPORTS,
    AssistantTurn,
    ModelCallError,
    RequestOptions,
    WireCall,
)
from supervisor.models.transports.base import DEFAULT_MAX_RETRIES, Body
from supervisor.seams.core.types import ChatMessage, Completion, StreamEvent

type Effort = ThinkingLevel | Literal["auto"]

_FINISH = {"stop": "stop", "length": "length", "toolUse": "tool_calls"}


class ModelUnavailable(Exception):
    """No model by that id, or no way to reach it from here."""

    status = 404
    type = "model_unavailable"
    code = "model_unavailable"
    retryable = False


class UnsupportedAlias(ValueError):
    """``MODEL_GATEWAY_MODELS`` names a model the native registry does not define."""


class ModelRouter:
    """Resolves model ids to specs and routes them through the gateway."""

    def __init__(
        self,
        config: GatewayConfig,
        *,
        transport: httpx.BaseTransport | None = None,
        audit: ModelCallAudit | None = None,
    ) -> None:
        unknown = [source for source in config.aliases if resolve_builtin(source) is None]
        if unknown:
            raise UnsupportedAlias(
                f"MODEL_GATEWAY_MODELS contains unsupported models: {', '.join(unknown)}"
            )
        self.config = config
        self.catalog = GatewayCatalog(config, transport=transport)
        self.audit = audit or ModelCallAudit()
        self._transport = transport

    def serves(self, base_url: str) -> bool:
        """Whether ``base_url`` names this router's gateway (with or without ``/v1``)."""
        return _origin_path(base_url) == _origin_path(self.config.url)

    def http_client(self) -> httpx.Client:
        return httpx.Client(transport=self._transport, follow_redirects=False)

    def refresh(self) -> None:
        self.catalog.refresh()

    def resolve(self, model_id: str) -> ModelSpec:
        if is_gateway_model_id(model_id):
            spec = self.catalog.admitted.resolve(model_id)
            if spec is None:
                raise GatewayModelUnavailable(model_id)
            return spec
        if (native := resolve_builtin(model_id)) is not None:
            return native
        if (legacy := self.catalog.admitted.resolve(GATEWAY_MODEL_PREFIX + model_id)) is not None:
            return legacy
        raise ModelUnavailable(f"Unsupported model: {model_id}")

    def route(self, spec: ModelSpec, *, api_key: str | None = None) -> GatewayRequest:
        request = gateway_request(self.catalog, spec, api_key=api_key)
        if request is None:
            raise ModelUnavailable(
                f"{spec.id} is not routed through the model gateway; "
                "map it in MODEL_GATEWAY_MODELS or select a gateway model"
            )
        return request

    def target_for(self, model_id: str) -> str | None:
        """The gateway model group a session's key must be allowed to call."""
        self.refresh()
        try:
            spec = self.resolve(model_id)
        except (ModelUnavailable, GatewayModelUnavailable):
            return None
        return self.catalog.models.get(spec.id)

    def selectable_models(self) -> list[ModelSpec]:
        """What a session may pick: discovered groups, then routed native models."""
        self.refresh()
        native = [
            spec
            for model_id, _ in self.catalog.models.items()
            if not is_gateway_model_id(model_id)
            and offered_in_picker(model_id)
            and (spec := resolve_builtin(model_id)) is not None
        ]
        return [*self.catalog.admitted.chat_models(), *native]


def _origin_path(url: str) -> str:
    parts = urlsplit(url.strip())
    return f"{parts.scheme.lower()}://{parts.netloc.lower()}{parts.path.rstrip('/').removesuffix('/v1')}"


@dataclass(slots=True)
class RoutedModelClient:
    """One session's model access (the ``model_gateway`` seam, plus ``ModelCatalog``)."""

    router: ModelRouter
    api_key: str | None = None
    """The session's virtual key; the configured gateway key when None."""
    session_id: str | None = None
    default_model: str | None = None
    effort: Effort = "auto"
    _http: httpx.Client | None = None
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def rekey(self, token: str) -> None:
        """Call with ``token`` from now on (the session's key was renewed)."""
        self.api_key = token

    def _client(self) -> httpx.Client:
        with self._lock:
            if self._http is None:
                self._http = self.router.http_client()
            return self._http

    def spec(self, model: str | None = None) -> ModelSpec:
        model_id = model or self.default_model
        if not model_id:
            raise ModelUnavailable("no model selected")
        self.router.refresh()
        return self.router.resolve(model_id)

    def context_window(self, model: str) -> int | None:
        try:
            return self.spec(model).context_window
        except (ModelUnavailable, GatewayModelUnavailable):
            return None

    def thinking_level(self, spec: ModelSpec) -> ThinkingLevel:
        level: ThinkingLevel = (
            default_thinking_level(spec) if self.effort == "auto" else self.effort
        )
        return spec.clamp_thinking_level(level)

    def turn(
        self,
        messages: Sequence[ChatMessage],
        tools: Sequence[Mapping[str, Any]],
        *,
        model: str | None = None,
    ) -> tuple[ModelSpec, AssistantTurn]:
        """One agent model call at the session's effort; the spec it resolved to and its turn."""
        spec = self.spec(model)
        return spec, self.call(
            spec, context_from_chat(messages, tools), thinking=self.thinking_level(spec)
        )

    def call(
        self,
        spec: ModelSpec,
        context: Context,
        *,
        thinking: ThinkingLevel = "off",
        max_tokens: int | None = None,
        tool_choice: Any = None,
        max_retries: int | None = None,
    ) -> AssistantTurn:
        """Send ``context`` to ``spec`` through the gateway.

        ``thinking`` is sent as given (an agent turn clamps the session's effort to
        the model first). The wire body names the gateway group, re-resolved after
        a last refresh, with the model's request overrides (an Ultrafast tier) on top.
        """
        routed = self.router.route(spec, api_key=self.api_key)
        override = request_override(spec.id)

        def wire_body(body: Body) -> Body:
            self.router.refresh()
            current = gateway_request(self.router.catalog, spec, api_key=self.api_key)
            if current is None:
                raise GatewayModelUnavailable(spec.id)
            extra = {"service_tier": override.service_tier} if override else {}
            return {**body, **extra, "model": current.target}

        options = RequestOptions(
            session_id=self.session_id,
            thinking=thinking,
            max_tokens=max_tokens,
            tool_choice=tool_choice,
            payload_hook=wire_body,
            max_retries=DEFAULT_MAX_RETRIES if max_retries is None else max_retries,
        )
        call = WireCall(
            spec=routed.spec,
            context=context,
            api_key=routed.api_key,
            http=self._client(),
            headers=routed.headers,
            options=options,
        )
        turn = TRANSPORTS[spec.api](call)
        self.router.audit.record(
            ModelCallRecord(
                at=time.time(),
                scope=self.session_id or "",
                model=spec.id,
                input_tokens=turn.usage.prompt_tokens or estimate_context_tokens(context),
                entry_count=len(context.messages),
            )
        )
        return turn

    def complete(
        self,
        messages: list[ChatMessage],
        tools: list[dict[str, Any]],
        *,
        model: str | None = None,
    ) -> Completion:
        spec, turn = self.turn(messages, tools, model=model)
        return completion(spec, turn)

    def stream(
        self,
        messages: list[ChatMessage],
        tools: list[dict[str, Any]],
        *,
        model: str | None = None,
    ) -> Iterator[StreamEvent]:
        done = self.complete(messages, tools, model=model)
        if done.message.content:
            yield StreamEvent(type="delta", delta=done.message.content, model=done.model)
        yield StreamEvent(type="done", finish_reason=done.finish_reason, model=done.model)

    def close(self) -> None:
        with self._lock:
            if self._http is not None:
                self._http.close()
                self._http = None


def completion(spec: ModelSpec, turn: AssistantTurn) -> Completion:
    """The seam's chat-completion view of a turn, with its native parts kept for replay."""
    text = "".join(b.text for b in turn.blocks if isinstance(b, TextBlock))
    calls = [
        {"id": b.id, "name": b.name, "arguments": dict(b.arguments)}
        for b in turn.blocks
        if isinstance(b, ToolCallBlock)
    ]
    usage = turn.usage
    cost = turn.cost.total
    message = ChatMessage(
        role="assistant",
        content=text,
        tool_calls=calls or None,
        parts=blocks_to_parts(turn.blocks, api=spec.api, model=spec.id),
        model=spec.id,
    )
    return Completion(
        message=message,
        model=spec.id,
        finish_reason=_FINISH[turn.stop_reason],
        usage={
            "prompt_tokens": usage.prompt_tokens,
            "completion_tokens": usage.output,
            "total_tokens": usage.total_tokens,
            "input_tokens": usage.input,
            "output_tokens": usage.output,
            "cache_read": usage.cache_read,
            "cache_write": usage.cache_write,
            "reasoning_tokens": usage.reasoning,
            "cost": cost,
            "cost_total": cost,
        },
        tool_calls=calls or None,
    )


__all__ = [
    "ModelCallError",
    "ModelRouter",
    "ModelUnavailable",
    "RoutedModelClient",
    "UnsupportedAlias",
    "completion",
]
