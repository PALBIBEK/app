"""FastAPI application — the YAL model gateway.

Serves the LiteLLM-compatible contract a QM-style client discovers and calls:

* ``GET /v1/models`` and ``GET /model_group/info`` — the routes a caller may use,
  with mode, provider labels, limits, prices and capability flags;
* ``POST /v1/chat/completions``, ``POST /v1/messages`` and ``POST /v1/responses`` —
  passthrough inference on the three wires, streaming or not.

``model`` on every inference call is a route id. Calls are metered per route and
charged to the caller's virtual key.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from typing import Annotated, Any

from fastapi import Body as JSONBody
from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse
from opentelemetry import trace
from opentelemetry.propagate import extract
from opentelemetry.trace import Span, SpanKind, Status, StatusCode
from pydantic import BaseModel, Field

from model_gateway.auth import Caller, authenticate, gateway_error, require_service_token
from model_gateway.config import Settings
from model_gateway.factory import build_provider_registry
from model_gateway.metering import cost_usd, normalize_usage
from model_gateway.otel import setup_tracing
from model_gateway.providers.base import (
    SSE,
    Body,
    Call,
    ProviderRegistry,
    ProviderUnconfigured,
    UpstreamError,
    Wire,
)
from model_gateway.routes import Route, RouteConfigError, RouteTable, route_view
from model_gateway.virtual_keys import MemoryVirtualKeyStore, MintRequest, VirtualKeyStore
from model_gateway.wire_codec import TokenUsage, UsageTap, response_usage


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = Settings()
    app.state.settings = settings
    app.state.registry = build_provider_registry(settings)
    app.state.virtual_keys = MemoryVirtualKeyStore()
    setup_tracing()
    yield


app = FastAPI(title="yal-model-gateway", version="0.1.0", lifespan=_lifespan)

COST_HEADER = "x-litellm-response-cost"
"""LiteLLM's response-cost header; carried on every non-streaming inference response."""

JSONObject = Annotated[dict[str, Any], JSONBody()]
"""A request body taken verbatim: the gateway relays it, so it never reshapes it."""

_REQUIRED_FIELD: dict[Wire, str] = {
    Wire.CHAT: "messages",
    Wire.MESSAGES: "messages",
    Wire.RESPONSES: "input",
}


class MintVirtualKeyRequest(BaseModel):
    tenant_id: str
    session_id: str
    principal_id: str
    allowed_models: list[str] = Field(default_factory=list)
    ttl_s: int = 3600
    max_budget_usd: float = 5.0
    scope_id: str | None = None


def _settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


def _registry(request: Request) -> ProviderRegistry:
    registry: ProviderRegistry = request.app.state.registry
    return registry


def _vk_store(request: Request) -> VirtualKeyStore:
    store: VirtualKeyStore = request.app.state.virtual_keys
    return store


def _routes(request: Request) -> RouteTable:
    try:
        return _settings(request).routes()
    except RouteConfigError as exc:
        raise gateway_error(500, "ROUTES_INVALID", str(exc)) from None


# -- tracing ---------------------------------------------------------------


@contextmanager
def _span(operation: str, route_id: str, traceparent: str | None) -> Iterator[Span]:
    """A ``gen_ai`` server span joined to the caller's W3C trace (a no-op without a provider)."""
    with trace.get_tracer("yal-model-gateway").start_as_current_span(
        operation,
        context=extract({"traceparent": traceparent}) if traceparent else None,
        kind=SpanKind.SERVER,
        attributes={
            "gen_ai.operation.name": "chat",
            "gen_ai.provider.name": "yal-model-gateway",
            "yal.route.id": route_id,
        },
    ) as span:
        yield span


def _mark_error(span: Span, error_type: str, status: int) -> None:
    span.set_status(Status(StatusCode.ERROR))
    span.set_attribute("error.type", error_type)
    span.set_attribute("http.status_code", status)


# -- liveness / readiness --------------------------------------------------


@app.get("/v1/healthz")
def healthz() -> dict[str, str]:
    """Liveness: process up. No deps, routes, or credentials."""
    return {"status": "ok"}


def _readyz_reason(request: Request) -> str | None:
    """A safe enum reason when not ready, else None."""
    settings = getattr(request.app.state, "settings", None)
    registry = getattr(request.app.state, "registry", None)
    if settings is None or registry is None:
        return "routes"
    if settings.auth_must_be_configured() and not settings.model_gateway_token.strip():
        return "auth"
    try:
        routes = settings.routes()
    except RouteConfigError:
        return "routes"
    for route in routes.values():
        provider = registry.get(route.provider)
        if provider is None:
            return "provider"
        if not provider.credential_ok():
            return "credentials"
    return None


@app.get("/v1/readyz")
def readyz(request: Request) -> JSONResponse:
    """Readiness: routes valid, providers registered, vendor keys set, auth configured."""
    reason = _readyz_reason(request)
    if reason is not None:
        return JSONResponse(status_code=503, content={"status": "not_ready", "reason": reason})
    return JSONResponse({"status": "ready"})


# -- virtual keys ----------------------------------------------------------


@app.post("/v1/virtual-keys")
def mint_virtual_key(request: Request, body: MintVirtualKeyRequest) -> dict[str, Any]:
    """Service-auth only. Returns a short-lived session virtual key (WP-M5)."""
    require_service_token(request)
    claims = _vk_store(request).mint(
        MintRequest(
            tenant_id=body.tenant_id,
            session_id=body.session_id,
            principal_id=body.principal_id,
            allowed_models=tuple(body.allowed_models),
            ttl_s=max(1, min(int(body.ttl_s), 86_400)),
            max_budget_usd=max(0.0, min(float(body.max_budget_usd), 1_000.0)),
            scope_id=body.scope_id,
        )
    )
    return {
        "key_id": claims.key_id,
        "token": claims.token,
        "expires_at": claims.expires_at,
        "session_id": claims.session_id,
        "tenant_id": claims.tenant_id,
        "allowed_models": list(claims.allowed_models),
        "max_budget_usd": claims.max_budget_usd,
    }


@app.post("/v1/virtual-keys/revoke")
def revoke_virtual_key(
    request: Request,
    session_id: str | None = Query(default=None),
    token: str | None = Query(default=None),
) -> dict[str, Any]:
    require_service_token(request)
    store = _vk_store(request)
    if token:
        store.revoke(token)
        return {"revoked": 1}
    if session_id:
        return {"revoked": store.revoke_session(session_id)}
    raise gateway_error(400, "BAD_ARGS", "session_id or token required")


# -- discovery -------------------------------------------------------------


def _visible(request: Request) -> list[tuple[str, Route]]:
    caller = authenticate(request)
    return [(rid, route) for rid, route in _routes(request).items() if caller.may_use(rid)]


@app.get("/v1/routes")
def list_routes(request: Request) -> dict[str, Any]:
    return {"routes": [route_view(rid, route) for rid, route in _visible(request)]}


@app.get("/v1/routes/{route_id}")
def get_route(route_id: str, request: Request) -> dict[str, Any]:
    """One route's metadata, so a caller can size its context to the model behind it."""
    authenticate(request, route_id=route_id)
    route = _routes(request).get(route_id)
    if route is None:
        raise gateway_error(404, "ROUTE_UNKNOWN", f"unknown route id: {route_id!r}")
    return route_view(route_id, route)


@app.get("/v1/models")
def list_models(request: Request) -> dict[str, Any]:
    """OpenAI model list: the route ids this caller may call."""
    return {
        "object": "list",
        "data": [
            {"id": rid, "object": "model", "created": 0, "owned_by": route.provider}
            for rid, route in _visible(request)
        ],
    }


@app.get("/model_group/info")
def model_group_info(request: Request) -> dict[str, Any]:
    """LiteLLM end-user model-group metadata for the routes this caller may call."""
    return {"data": [route.model_group_info(rid) for rid, route in _visible(request)]}


# -- inference -------------------------------------------------------------


def _resolve_route(request: Request, model: Any, route_header: str | None) -> tuple[str, Route]:
    route_id = (route_header or (model if isinstance(model, str) else "") or "").strip()
    route_id = route_id or _settings(request).model_gateway_default_route.strip()
    route = _routes(request).get(route_id)
    if route is None:
        raise gateway_error(404, "ROUTE_UNKNOWN", f"unknown route id: {route_id!r}")
    return route_id, route


def _transient(exc: UpstreamError) -> bool:
    return (
        exc.status is None
        or exc.status == 429
        or exc.status >= 500
        or isinstance(exc, ProviderUnconfigured)
    )


def _dispatch(
    request: Request, wire: Wire, body: Body, route_id: str, route: Route
) -> tuple[str, Route, Body | SSE]:
    """Call the route's provider, then each fallback route once, until one answers.

    A non-transient upstream rejection (a 4xx other than 429) is the caller's
    problem, so it is returned at once rather than retried elsewhere.
    """
    routes = _routes(request)
    chain = [route_id, *(r for r in dict.fromkeys(route.fallback_route_ids) if r != route_id)]
    failure: Exception | None = None
    for rid in chain:
        candidate = route if rid == route_id else routes.get(rid)
        if candidate is None:
            continue
        provider = _registry(request).get(candidate.provider)
        if provider is None:
            failure = failure or KeyError(f"provider not registered: {candidate.provider}")
            continue
        if wire not in provider.wires:
            if rid == route_id:
                raise gateway_error(
                    400,
                    "WIRE_UNSUPPORTED",
                    f"route {rid!r} ({candidate.provider}) does not serve /v1/{wire.value.split('.')[0]}",
                )
            continue
        upstream_body = {
            **body,
            "model": candidate.upstream_model,
            "stream": body.get("stream") is True,
        }
        if wire is Wire.CHAT and upstream_body["stream"]:
            upstream_body.setdefault("stream_options", {"include_usage": True})
        try:
            return (
                rid,
                candidate,
                provider.call(Call(wire, upstream_body, retries=candidate.retries)),
            )
        except UpstreamError as exc:
            if not _transient(exc):
                raise gateway_error(exc.status or 400, "UPSTREAM_REJECTED", str(exc)) from exc
            failure = exc
    if isinstance(failure, KeyError):
        raise gateway_error(500, "PROVIDER_MISSING", str(failure))
    raise gateway_error(502, "UPSTREAM_ERROR", str(failure) if failure else "upstream failed")


def _charge(store: VirtualKeyStore, caller: Caller, cost: float) -> None:
    if caller.virtual_key and cost > 0:
        store.add_spend(caller.virtual_key, cost)


def _metered(
    stream: SSE, wire: Wire, route: Route, caller: Caller, store: VirtualKeyStore
) -> Iterator[bytes]:
    tap = UsageTap(wire)
    try:
        for chunk in stream:
            tap.feed(chunk)
            yield chunk
    finally:
        _charge(store, caller, cost_usd(tap.usage() or TokenUsage(), route))


def _infer(
    request: Request,
    wire: Wire,
    body: Body,
    *,
    route_header: str | None,
    traceparent: str | None,
) -> Any:
    if not isinstance(body, dict):
        raise gateway_error(400, "BAD_ARGS", "request body must be a JSON object")
    field = _REQUIRED_FIELD[wire]
    if not body.get(field):
        raise gateway_error(400, "BAD_ARGS", f"{field} required")
    route_id, route = _resolve_route(request, body.get("model"), route_header)
    caller = authenticate(request, route_id=route_id)
    if route.mode != "chat":
        raise gateway_error(400, "ROUTE_MODE", f"route {route_id!r} is an {route.mode} model")
    store = _vk_store(request)
    with _span(wire.value, route_id, traceparent) as span:
        try:
            used_id, used, result = _dispatch(request, wire, body, route_id, route)
        except HTTPException as exc:
            _mark_error(span, "HTTPException", exc.status_code)
            raise
        span.set_attribute("gen_ai.response.model", used_id)
        if not isinstance(result, dict):
            return StreamingResponse(
                _metered(result, wire, used, caller, store), media_type="text/event-stream"
            )
        usage = response_usage(wire, result) or TokenUsage()
        cost = cost_usd(usage, used)
        _charge(store, caller, cost)
        span.set_attributes(
            {
                "gen_ai.usage.input_tokens": usage.prompt_tokens,
                "gen_ai.usage.output_tokens": usage.output,
                "yal.cost.usd": cost,
            }
        )
        if wire is Wire.CHAT:
            result = {
                **result,
                "model": used_id,
                "usage": normalize_usage(result.get("usage"), cost=cost),
            }
        return JSONResponse(result, headers={COST_HEADER: f"{cost:.10f}"})


@app.post("/v1/chat/completions")
def chat_completions(
    request: Request,
    body: JSONObject,
    x_yal_route_id: str | None = Header(default=None, alias="X-YAL-Route-Id"),
    traceparent: str | None = Header(default=None),
) -> Any:
    return _infer(request, Wire.CHAT, body, route_header=x_yal_route_id, traceparent=traceparent)


@app.post("/v1/messages")
def messages(
    request: Request,
    body: JSONObject,
    x_yal_route_id: str | None = Header(default=None, alias="X-YAL-Route-Id"),
    traceparent: str | None = Header(default=None),
) -> Any:
    """Anthropic Messages passthrough: thinking blocks and their signatures round-trip intact."""
    return _infer(
        request, Wire.MESSAGES, body, route_header=x_yal_route_id, traceparent=traceparent
    )


@app.post("/v1/responses")
def responses(
    request: Request,
    body: JSONObject,
    x_yal_route_id: str | None = Header(default=None, alias="X-YAL-Route-Id"),
    traceparent: str | None = Header(default=None),
) -> Any:
    """OpenAI Responses passthrough: encrypted reasoning and ``service_tier`` reach the upstream."""
    return _infer(
        request, Wire.RESPONSES, body, route_header=x_yal_route_id, traceparent=traceparent
    )
