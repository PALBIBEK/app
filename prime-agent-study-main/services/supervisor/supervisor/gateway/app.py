"""FastAPI application — the PRIME model gateway.

Serves the LiteLLM-compatible contract a QM-style client discovers and calls:

* ``GET /v1/models`` and ``GET /model_group/info`` — the routes a caller may use,
  with mode, provider labels, limits, prices and capability flags;
* ``POST /v1/chat/completions``, ``POST /v1/messages`` and ``POST /v1/responses`` —
  passthrough inference on the three wires, streaming or not;
* ``PUT /v1/vendors/{vendor}/base-url`` — a local server's address, set from the product.
* ``GET /v1/vendors`` and ``PUT``/``DELETE /v1/vendors/{vendor}/key`` — the vendors, whether
  each has a key, and setting or removing one (service token only; a key never comes back).

``model`` on every inference call is a route id. Calls are metered per route and
charged to the caller's virtual key.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from typing import Annotated, Any

from fastapi import Body as JSONBody
from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse, PlainTextResponse, StreamingResponse
from opentelemetry import trace
from opentelemetry.propagate import extract
from opentelemetry.trace import Span, SpanKind, Status, StatusCode
from pydantic import BaseModel, Field

from supervisor.gateway.auth import Caller, authenticate, gateway_error, require_service_token
from supervisor.gateway.config import Settings
from supervisor.gateway.factory import build_provider_registry
from supervisor.gateway.key_check import KeyCheck, check_key
from supervisor.gateway.local_models import LOCAL_PREFIX, LocalModels
from supervisor.gateway.metering import cost_usd, normalize_usage
from supervisor.gateway.metrics import CountModelCalls, ModelCalls, Spend, prometheus
from supervisor.gateway.otel import setup_tracing
from supervisor.gateway.providers.base import (
    SSE,
    Body,
    Call,
    ProviderRegistry,
    ProviderUnconfigured,
    UpstreamError,
    Wire,
)
from supervisor.gateway.routes import Route, RouteConfigError, RouteTable, route_view
from supervisor.gateway.vendor_keys import VendorKeys
from supervisor.gateway.vendors import VENDORS, Vendor, parse_base_url
from supervisor.gateway.virtual_keys import (
    KEY_TTL_S,
    MintRequest,
    SignedVirtualKeyStore,
    VirtualKeyStore,
)
from supervisor.gateway.wire_codec import TokenUsage, UsageTap, response_usage


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = Settings()
    app.state.settings = settings
    app.state.keys = settings.vendor_key_store()
    app.state.registry = build_provider_registry(settings, app.state.keys)
    app.state.local_models = LocalModels(
        app.state.keys.address_source(VENDORS["local"], settings.local_model_base_url),
        app.state.keys.source("local"),
        context_window=settings.local_model_context_window,
    )
    # Signed keys: a restart (a deploy) or another replica takes the keys sessions hold.
    app.state.virtual_keys = SignedVirtualKeyStore(settings.virtual_key_secret())
    setup_tracing()
    yield


app = FastAPI(title="prime-model-gateway", version="0.1.0", lifespan=_lifespan)

MODEL_CALLS = ModelCalls()
SPEND = Spend()
app.add_middleware(CountModelCalls, calls=MODEL_CALLS)

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
    ttl_s: int = KEY_TTL_S
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


def _keys(request: Request) -> VendorKeys:
    keys: VendorKeys = request.app.state.keys
    return keys


def _routes(request: Request) -> RouteTable:
    """The configured routes, each keyed vendor's defaults, then the local server's models."""
    try:
        table = _settings(request).routes(_keys(request).keyed())
    except RouteConfigError as exc:
        raise gateway_error(500, "ROUTES_INVALID", str(exc)) from None
    local: LocalModels = request.app.state.local_models
    return table.with_defaults(local.routes())


# -- tracing ---------------------------------------------------------------


@contextmanager
def _span(operation: str, route_id: str, traceparent: str | None) -> Iterator[Span]:
    """A ``gen_ai`` server span joined to the caller's W3C trace (a no-op without a provider)."""
    with trace.get_tracer("prime-model-gateway").start_as_current_span(
        operation,
        context=extract({"traceparent": traceparent}) if traceparent else None,
        kind=SpanKind.SERVER,
        attributes={
            "gen_ai.operation.name": "chat",
            "gen_ai.provider.name": "prime-model-gateway",
            "prime.route.id": route_id,
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
    keys = getattr(request.app.state, "keys", None)
    if settings is None or registry is None or keys is None:
        return "routes"
    if settings.auth_must_be_configured() and not settings.model_gateway_token.strip():
        return "auth"
    try:
        routes = settings.routes(keys.keyed())
    except RouteConfigError:
        return "routes"
    for route in routes.values():
        provider = registry.get(route.provider)
        if provider is None:
            return "provider"
        if not provider.credential_ok():
            return "credentials"
    return None


@app.get("/v1/metrics", response_class=PlainTextResponse)
def metrics() -> PlainTextResponse:
    """Model calls by status code and spend, as Prometheus counters (counts only)."""
    return PlainTextResponse(prometheus(MODEL_CALLS, SPEND), media_type="text/plain; version=0.0.4")


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


# -- vendors ---------------------------------------------------------------


class VendorKeyIn(BaseModel):
    key: str = Field(min_length=1, max_length=4096)
    updated_by: str = Field(min_length=1, max_length=255)
    """Who set it (the product's principal), kept with the key's status."""


def _vendor(vendor_id: str) -> Vendor:
    vendor = VENDORS.get(vendor_id)
    if vendor is None:
        raise gateway_error(404, "VENDOR_UNKNOWN", f"unknown vendor: {vendor_id!r}")
    return vendor


def _vendor_view(request: Request, vendor: Vendor) -> dict[str, Any]:
    """A vendor as the product shows it: whether it can be called, never its key."""
    status = _keys(request).status(vendor.id)
    provider = _registry(request).get(vendor.id)
    view: dict[str, Any] = {
        "id": vendor.id,
        "label": vendor.label,
        "base_url": _base_url(request, vendor),
        "key_required": vendor.key_required,
        "key_checked": vendor.key_check is not None,
        "configured": status.configured,
        "source": status.source,
        "updated_at": status.updated_at,
        "updated_by": status.updated_by,
        "ready": provider is not None and provider.credential_ok(),
        "address_settable": vendor.address_settable,
    }
    if vendor.address_settable:  # a local server: where it is, and what it runs
        address = _keys(request).address(vendor, _settings(request).vendor_base_url(vendor))
        local: LocalModels = request.app.state.local_models
        models = sorted(rid.removeprefix(LOCAL_PREFIX) for rid in local.routes())
        view |= {
            "address_source": address.source,
            "models": models,
            "unreachable": local.error() if address.base_url else None,
        }
    return view


def _base_url(request: Request, vendor: Vendor) -> str:
    """The address the gateway calls ``vendor`` at now."""
    environment = _settings(request).vendor_base_url(vendor)
    return _keys(request).address(vendor, environment).base_url if vendor.address_settable else environment


def _editable(request: Request) -> VendorKeys:
    keys = _keys(request)
    if not keys.editable:
        raise gateway_error(
            409, "KEYS_NOT_EDITABLE", "vendor keys are set in the gateway's environment here"
        )
    return keys


@app.get("/v1/vendors")
def list_vendors(request: Request) -> dict[str, Any]:
    """Every vendor, whether it has a key and where from, and whether keys can be set here."""
    require_service_token(request)
    return {
        "editable": _keys(request).editable,
        "vendors": [_vendor_view(request, vendor) for vendor in VENDORS.values()],
    }


@app.put("/v1/vendors/{vendor_id}/key")
def set_vendor_key(vendor_id: str, body: VendorKeyIn, request: Request) -> dict[str, Any]:
    """Check the key with the vendor, then keep it: a key the vendor refuses is not saved."""
    require_service_token(request)
    vendor, keys = _vendor(vendor_id), _editable(request)
    key = body.key.strip()
    checked = check_key(vendor, key, _base_url(request, vendor))
    if checked is KeyCheck.REFUSED:
        raise gateway_error(400, "KEY_REFUSED", f"{vendor.label} refused this key")
    if checked is KeyCheck.UNREACHABLE:
        raise gateway_error(
            502, "KEY_UNCHECKED", f"could not reach {vendor.label} to check the key; try again"
        )
    keys.set(vendor, key, by=body.updated_by)
    request.app.state.local_models.expire()  # a local server's list may need the key
    return {**_vendor_view(request, vendor), "checked": checked is KeyCheck.ACCEPTED}


@app.delete("/v1/vendors/{vendor_id}/key")
def remove_vendor_key(
    vendor_id: str, request: Request, updated_by: str = Query(min_length=1, max_length=255)
) -> dict[str, Any]:
    """Remove the vendor's key: it is off from now, even with a key in the environment."""
    require_service_token(request)
    vendor = _vendor(vendor_id)
    _editable(request).remove(vendor, by=updated_by)
    return _vendor_view(request, vendor)


class VendorAddressIn(BaseModel):
    base_url: str = Field(max_length=2048)
    """An http(s) URL with the API's version prefix (Ollama: ``http://host:11434/v1``); empty
    turns the vendor off."""
    updated_by: str = Field(min_length=1, max_length=255)


@app.put("/v1/vendors/{vendor_id}/base-url")
def set_vendor_address(vendor_id: str, body: VendorAddressIn, request: Request) -> dict[str, Any]:
    """Set the address of a vendor whose address the product may set (a local server): its
    models are read from there at once. A key set for its old address is dropped."""
    require_service_token(request)
    vendor, keys = _vendor(vendor_id), _editable(request)
    if not vendor.address_settable:
        raise gateway_error(403, "ADDRESS_FIXED", f"{vendor.label}'s address is the deployment's")
    try:
        base_url = parse_base_url("the address", body.base_url)
    except ValueError as exc:
        raise gateway_error(400, "ADDRESS_INVALID", str(exc)) from None
    keys.set_address(vendor, base_url, by=body.updated_by)
    request.app.state.local_models.expire()
    return _vendor_view(request, vendor)


# -- discovery -------------------------------------------------------------


def _ready(request: Request, route: Route) -> bool:
    provider = _registry(request).get(route.provider)
    return provider is not None and provider.credential_ok()


def _visible(request: Request, *, callable_only: bool = False) -> list[tuple[str, Route]]:
    """The routes this caller may use; with ``callable_only``, those that can be called now
    (a route whose vendor has no key or URL is left out, as QM offers only the models of
    configured providers)."""
    caller = authenticate(request)
    return [
        (rid, route)
        for rid, route in _routes(request).items()
        if caller.may_use(rid) and (not callable_only or _ready(request, route))
    ]


@app.get("/v1/routes")
def list_routes(request: Request) -> dict[str, Any]:
    """Every route the caller may use, callable now or not: what each one calls."""
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
    """OpenAI model list: the route ids this caller may call, that can be called now."""
    return {
        "object": "list",
        "data": [
            {"id": rid, "object": "model", "created": 0, "owned_by": route.provider}
            for rid, route in _visible(request, callable_only=True)
        ],
    }


@app.get("/model_group/info")
def model_group_info(request: Request) -> dict[str, Any]:
    """LiteLLM end-user model-group metadata for the routes ``/v1/models`` lists."""
    return {
        "data": [
            route.model_group_info(rid) for rid, route in _visible(request, callable_only=True)
        ]
    }


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
    """Count ``cost`` toward the platform's spend, and the caller's key's."""
    if cost <= 0:
        return
    SPEND.add(cost)
    if caller.virtual_key:
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
                "prime.cost.usd": cost,
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
    x_prime_route_id: str | None = Header(default=None, alias="X-PRIME-Route-Id"),
    traceparent: str | None = Header(default=None),
) -> Any:
    return _infer(request, Wire.CHAT, body, route_header=x_prime_route_id, traceparent=traceparent)


@app.post("/v1/messages")
def messages(
    request: Request,
    body: JSONObject,
    x_prime_route_id: str | None = Header(default=None, alias="X-PRIME-Route-Id"),
    traceparent: str | None = Header(default=None),
) -> Any:
    """Anthropic Messages passthrough: thinking blocks and their signatures round-trip intact."""
    return _infer(
        request, Wire.MESSAGES, body, route_header=x_prime_route_id, traceparent=traceparent
    )


@app.post("/v1/responses")
def responses(
    request: Request,
    body: JSONObject,
    x_prime_route_id: str | None = Header(default=None, alias="X-PRIME-Route-Id"),
    traceparent: str | None = Header(default=None),
) -> Any:
    """OpenAI Responses passthrough: encrypted reasoning and ``service_tier`` reach the upstream."""
    return _infer(
        request, Wire.RESPONSES, body, route_header=x_prime_route_id, traceparent=traceparent
    )
