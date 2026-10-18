"""FastAPI application — OpenAI-compat surface for YAL model-gateway."""

from __future__ import annotations

import hmac
import json
import re
from collections.abc import Iterator
from contextlib import nullcontext
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from model_gateway.config import Settings
from model_gateway.factory import build_provider_registry
from model_gateway.metering import stamp_completion_usage, usage_cost_usd
from model_gateway.providers.base import ProviderRegistry
from model_gateway.virtual_keys import (
    MemoryVirtualKeyStore,
    MintRequest,
    VirtualKeyStore,
    enforce_virtual_key_budget,
    verify_virtual_key,
)

app = FastAPI(title="yal-model-gateway", version="0.1.0")

_USAGE_IN_SSE = re.compile(r'"usage"\s*:')


class ChatCompletionRequest(BaseModel):
    model: str | None = None
    messages: list[dict[str, Any]] = Field(default_factory=list)
    tools: list[dict[str, Any]] | None = None
    stream: bool = False


class MintVirtualKeyRequest(BaseModel):
    tenant_id: str
    session_id: str
    principal_id: str
    allowed_models: list[str] = Field(default_factory=list)
    ttl_s: int = 3600
    max_budget_usd: float = 5.0
    scope_id: str | None = None


def _settings(request: Request) -> Settings:
    return request.app.state.settings


def _registry(request: Request) -> ProviderRegistry:
    return request.app.state.registry


def _vk_store(request: Request) -> VirtualKeyStore:
    return request.app.state.virtual_keys


def _bearer(authorization: str | None) -> str | None:
    if not authorization or not authorization.startswith("Bearer "):
        return None
    return authorization.removeprefix("Bearer ").strip() or None


def _require_service_token(request: Request, authorization: str | None) -> None:
    """Mint/admin paths require the shared service token (not a virtual key)."""
    token = _settings(request).model_gateway_token
    if not token:
        raise HTTPException(
            status_code=503,
            detail={
                "error": {
                    "code": "AUTH_UNCONFIGURED",
                    "message": "MODEL_GATEWAY_TOKEN required to mint virtual keys",
                }
            },
        )
    got = _bearer(authorization)
    if got is None or not _bearer_matches(got, token):
        raise HTTPException(
            status_code=401,
            detail={"error": {"code": "AUTH_INVALID", "message": "service token required"}},
        )


def _bearer_matches(raw: str, expected: str) -> bool:
    if len(raw) != len(expected):
        return False
    return hmac.compare_digest(raw, expected)


def _check_auth(
    request: Request,
    authorization: str | None,
    *,
    route_id: str | None = None,
) -> str | None:
    """Accept shared service token or a live virtual key when auth is configured.

    Fail closed when AUTH_REQUIRED / production|staging and token unset (Stage 3 / D1).
    Returns the virtual-key token when auth used a VK; else None.
    """
    settings = _settings(request)
    service = settings.model_gateway_token
    if not service:
        if settings.auth_must_be_configured():
            raise HTTPException(
                status_code=503,
                detail={
                    "error": {
                        "code": "AUTH_UNCONFIGURED",
                        "message": "MODEL_GATEWAY_TOKEN required",
                    }
                },
            )
        return None
    got = _bearer(authorization)
    if got is None:
        raise HTTPException(
            status_code=401,
            detail={"error": {"code": "AUTH_REQUIRED", "message": "Bearer token required"}},
        )
    if _bearer_matches(got, service):
        return None
    if got.startswith("vk_"):
        try:
            verify_virtual_key(_vk_store(request), got, route_id=route_id)
            enforce_virtual_key_budget(_vk_store(request), got)
        except LookupError:
            raise HTTPException(
                status_code=401,
                detail={"error": {"code": "AUTH_INVALID", "message": "unknown virtual key"}},
            ) from None
        except PermissionError as exc:
            code = str(exc) or "VIRTUAL_KEY_DENIED"
            raise HTTPException(
                status_code=403,
                detail={"error": {"code": code, "message": code.lower().replace("_", " ")}},
            ) from None
        return got
    raise HTTPException(
        status_code=401,
        detail={"error": {"code": "AUTH_INVALID", "message": "invalid bearer token"}},
    )


def _resolve_route(
    request: Request,
    *,
    body_model: str | None,
    route_header: str | None,
) -> tuple[str, dict[str, Any]]:
    settings = _settings(request)
    route_id = (route_header or body_model or settings.model_gateway_default_route or "").strip()
    routes = settings.routes()
    if route_id not in routes:
        raise HTTPException(
            status_code=404,
            detail={
                "error": {
                    "code": "ROUTE_UNKNOWN",
                    "message": f"unknown route id: {route_id!r}",
                }
            },
        )
    return route_id, routes[route_id]


def _sse_chunks(payload: dict[str, Any]) -> Iterator[str]:
    completion_id = payload["id"]
    model = payload["model"]
    created = payload["created"]
    for piece in payload.get("chunks", []):
        chunk = {
            "id": completion_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": model,
            "choices": [{"index": 0, "delta": {"content": piece}, "finish_reason": None}],
        }
        yield f"data: {json.dumps(chunk)}\n\n"
    done = {
        "id": completion_id,
        "object": "chat.completion.chunk",
        "created": created,
        "model": model,
        "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
    }
    # Attach invented usage/cost on final echo chunk for stream metering.
    usage = payload.get("usage")
    if usage:
        done["usage"] = usage
    yield f"data: {json.dumps(done)}\n\n"
    yield "data: [DONE]\n\n"


def _extract_usage_from_sse_text(buf: str) -> dict[str, Any] | None:
    """Best-effort: find last JSON object in SSE buffer that carries usage."""
    last: dict[str, Any] | None = None
    for line in buf.splitlines():
        line = line.strip()
        if not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if not data or data == "[DONE]":
            continue
        if not _USAGE_IN_SSE.search(data):
            continue
        try:
            obj = json.loads(data)
        except json.JSONDecodeError:
            continue
        usage = obj.get("usage")
        if isinstance(usage, dict):
            last = usage
    return last


def _relay_upstream_sse(
    byte_iter: Iterator[bytes],
    *,
    vk_token: str | None,
    store: VirtualKeyStore,
) -> Iterator[bytes]:
    """Pass-through upstream SSE; record VK spend from final usage if present."""
    chunks: list[bytes] = []
    try:
        for chunk in byte_iter:
            chunks.append(chunk)
            yield chunk
    finally:
        if vk_token:
            text = b"".join(chunks).decode("utf-8", errors="replace")
            usage = _extract_usage_from_sse_text(text)
            if usage:
                from model_gateway.metering import normalize_usage

                cost = usage_cost_usd(normalize_usage(usage, invent_cost=True))
                if cost > 0:
                    store.add_spend(vk_token, cost)


def _provider_retries(meta: dict[str, Any]) -> int:
    """Route ``retries`` = additional attempts on transient 429 (default 1)."""
    raw = meta.get("retries")
    if raw is None:
        return 1
    try:
        return max(0, int(raw))
    except (TypeError, ValueError):
        return 1


def _call_provider(
    request: Request,
    *,
    route_id: str,
    meta: dict[str, Any],
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None,
    stream: bool,
) -> tuple[str, dict[str, Any]]:
    """Invoke provider; on failure try ``fallback_route_ids`` once each."""
    settings = _settings(request)
    routes = settings.routes()
    chain = [route_id]
    for fb in meta.get("fallback_route_ids") or []:
        fb_id = str(fb).strip()
        if fb_id and fb_id not in chain:
            chain.append(fb_id)

    last_exc: Exception | None = None
    for rid in chain:
        rmeta = routes.get(rid) if rid != route_id else meta
        if rmeta is None:
            rmeta = routes.get(rid)
        if rmeta is None:
            continue
        provider_name = str(rmeta.get("provider") or "")
        upstream_model = str(rmeta.get("upstream_model") or rid)
        try:
            provider = _registry(request).get(provider_name)
        except KeyError as exc:
            last_exc = exc
            continue
        # Apply per-route retry hint when provider supports it.
        retries = _provider_retries(rmeta)
        if hasattr(provider, "_retries"):
            provider._retries = retries  # noqa: SLF001 — intentional test/route knob
        try:
            result = provider.complete(
                messages=messages,
                tools=tools,
                upstream_model=upstream_model,
                stream=stream,
            )
            return rid, result
        except NotImplementedError:
            raise
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            continue
    if isinstance(last_exc, KeyError):
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": "PROVIDER_MISSING", "message": str(last_exc)}},
        ) from last_exc
    raise HTTPException(
        status_code=502,
        detail={
            "error": {
                "code": "UPSTREAM_ERROR",
                "message": str(last_exc) if last_exc else "upstream failed",
            }
        },
    ) from last_exc


@app.on_event("startup")
def _startup() -> None:
    settings = Settings()
    app.state.settings = settings
    app.state.registry = build_provider_registry(settings)
    app.state.virtual_keys = MemoryVirtualKeyStore()
    # D13 B: TracerProvider + OTLP when endpoint set (soft-fail if collector down).
    try:
        from model_gateway.otel import setup_tracing

        setup_tracing()
    except Exception:
        pass


@app.get("/v1/healthz")
def healthz() -> dict[str, str]:
    """Liveness: process up. No deps, routes, or credentials."""
    return {"status": "ok"}


def _provider_credential_ok(settings: Settings, provider_name: str) -> bool:
    """Return True when a non-echo provider has its required API key set."""
    if provider_name == "echo":
        return True
    if provider_name == "openrouter":
        return bool(settings.openrouter_api_key.strip())
    if provider_name == "openai":
        return bool(settings.openai_api_key.strip())
    # Unknown provider name: treat as needing registration only (credentials N/A).
    return True


def _readyz_reason(request: Request) -> str | None:
    """Return a safe enum reason if not ready, else None."""
    settings = getattr(request.app.state, "settings", None)
    registry = getattr(request.app.state, "registry", None)
    if settings is None or registry is None:
        return "routes"

    if settings.auth_must_be_configured() and not settings.model_gateway_token.strip():
        return "auth"

    try:
        routes = settings.routes()
    except Exception:
        return "routes"
    if not routes:
        return "routes"

    for _rid, meta in routes.items():
        provider_name = str(meta.get("provider") or "").strip()
        if not provider_name:
            return "provider"
        try:
            registry.get(provider_name)
        except KeyError:
            return "provider"
        if not _provider_credential_ok(settings, provider_name):
            return "credentials"
    return None


@app.get("/v1/readyz")
def readyz(request: Request) -> JSONResponse:
    """Readiness: routes registered, providers present, non-echo keys set.

    Echo-only route profiles are ready without vendor API keys.
    AUTH_REQUIRED / staging / prod without MODEL_GATEWAY_TOKEN → not ready.
    """
    reason = _readyz_reason(request)
    if reason is not None:
        return JSONResponse(
            status_code=503,
            content={"status": "not_ready", "reason": reason},
        )
    return JSONResponse({"status": "ready"})


@app.post("/v1/virtual-keys")
def mint_virtual_key(
    request: Request,
    body: MintVirtualKeyRequest,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    """Service-auth only. Returns a short-lived session virtual key (WP-M5)."""
    _require_service_token(request, authorization)
    ttl_s = max(1, min(int(body.ttl_s), 86_400))
    max_budget_usd = max(0.0, min(float(body.max_budget_usd), 1_000.0))
    claims = _vk_store(request).mint(
        MintRequest(
            tenant_id=body.tenant_id,
            session_id=body.session_id,
            principal_id=body.principal_id,
            allowed_models=tuple(body.allowed_models),
            ttl_s=ttl_s,
            max_budget_usd=max_budget_usd,
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
    authorization: str | None = Header(default=None),
    session_id: str | None = Query(default=None),
    token: str | None = Query(default=None),
) -> dict[str, Any]:
    _require_service_token(request, authorization)
    store = _vk_store(request)
    if token:
        store.revoke(token)
        return {"revoked": 1}
    if session_id:
        return {"revoked": store.revoke_session(session_id)}
    raise HTTPException(
        status_code=400,
        detail={"error": {"code": "BAD_ARGS", "message": "session_id or token required"}},
    )


@app.get("/v1/routes")
def list_routes(
    request: Request,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    _check_auth(request, authorization)
    routes = _settings(request).routes()
    return {
        "routes": [
            {
                "id": rid,
                "provider": meta.get("provider"),
                "upstream_model": meta.get("upstream_model"),
            }
            for rid, meta in routes.items()
        ]
    }


@app.post("/v1/chat/completions")
def chat_completions(
    request: Request,
    body: ChatCompletionRequest,
    authorization: str | None = Header(default=None),
    x_yal_route_id: str | None = Header(default=None, alias="X-YAL-Route-Id"),
    traceparent: str | None = Header(default=None),
) -> Any:
    # D13 High: child span for chat completions with W3C propagation when present.
    span_cm = nullcontext()
    try:
        from opentelemetry import trace
        from opentelemetry.propagate import extract
        from opentelemetry.trace import SpanKind, Status, StatusCode

        carrier: dict[str, str] = {}
        if traceparent:
            carrier["traceparent"] = traceparent
        parent_ctx = extract(carrier) if carrier else None
        tracer = trace.get_tracer("yal-model-gateway")
        span_cm = tracer.start_as_current_span(
            "chat.completions",
            context=parent_ctx,
            kind=SpanKind.SERVER,
            attributes={
                "gen_ai.operation.name": "chat",
                "gen_ai.provider.name": "yal-model-gateway",
                "yal.route.id": x_yal_route_id or body.model or "",
            },
        )
    except Exception:
        span_cm = nullcontext()

    with span_cm as span:
        return _chat_completions_impl(
            request,
            body,
            authorization=authorization,
            x_yal_route_id=x_yal_route_id,
            span=span if span is not None else None,
        )


def _chat_completions_impl(
    request: Request,
    body: ChatCompletionRequest,
    *,
    authorization: str | None,
    x_yal_route_id: str | None,
    span: Any | None = None,
) -> Any:
    if not body.messages:
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": "BAD_ARGS", "message": "messages required"}},
        )
    route_id, meta = _resolve_route(
        request, body_model=body.model, route_header=x_yal_route_id
    )
    vk_token = _check_auth(request, authorization, route_id=route_id)
    store = _vk_store(request)

    try:
        used_route, result = _call_provider(
            request,
            route_id=route_id,
            meta=meta,
            messages=body.messages,
            tools=body.tools,
            stream=body.stream,
        )
    except HTTPException as exc:
        if span is not None:
            try:
                from opentelemetry.trace import Status, StatusCode

                span.set_status(Status(StatusCode.ERROR))
                span.set_attribute("error.type", "HTTPException")
                span.set_attribute("http.status_code", exc.status_code)
            except Exception:
                pass
        raise
    except NotImplementedError as exc:
        if span is not None:
            try:
                from opentelemetry.trace import Status, StatusCode

                span.set_status(Status(StatusCode.ERROR))
                span.set_attribute("error.type", "NotImplementedError")
            except Exception:
                pass
        raise HTTPException(
            status_code=501,
            detail={"error": {"code": "UNSUPPORTED", "message": str(exc)}},
        ) from exc

    if body.stream and isinstance(result, dict) and result.get("stream"):
        if result.get("upstream_sse") and callable(result.get("sse_iter")):
            return StreamingResponse(
                _relay_upstream_sse(
                    result["sse_iter"](),
                    vk_token=vk_token,
                    store=store,
                ),
                media_type="text/event-stream",
            )
        # Echo-style synthetic chunks — stamp cost onto payload for final SSE usage.
        invent = stamp_completion_usage(
            {
                "usage": {
                    "prompt_tokens": 1,
                    "completion_tokens": 1,
                    "total_tokens": 2,
                }
            },
            invent_cost=True,
        )["usage"]
        result = {**result, "usage": invent, "model": used_route}
        if span is not None:
            try:
                span.set_attribute("gen_ai.response.model", used_route)
                span.set_attribute("gen_ai.usage.input_tokens", invent.get("prompt_tokens", 0))
                span.set_attribute(
                    "gen_ai.usage.output_tokens", invent.get("completion_tokens", 0)
                )
                span.set_attribute("yal.cost.usd", float(usage_cost_usd(invent) or 0.0))
            except Exception:
                pass

        def _echo_and_spend() -> Iterator[str]:
            yield from _sse_chunks(result)
            if vk_token:
                cost = usage_cost_usd(invent)
                if cost > 0:
                    store.add_spend(vk_token, cost)

        return StreamingResponse(_echo_and_spend(), media_type="text/event-stream")

    # Non-stream: normalize usage + cost fields supervisor reads.
    if isinstance(result, dict):
        result = stamp_completion_usage(result, invent_cost=True)
        result = {**result, "model": used_route}
        if span is not None:
            try:
                usage = result.get("usage") or {}
                span.set_attribute("gen_ai.response.model", used_route)
                span.set_attribute(
                    "gen_ai.usage.input_tokens", int(usage.get("prompt_tokens") or 0)
                )
                span.set_attribute(
                    "gen_ai.usage.output_tokens", int(usage.get("completion_tokens") or 0)
                )
                span.set_attribute("yal.cost.usd", float(usage_cost_usd(usage) or 0.0))
            except Exception:
                pass
        if vk_token:
            cost = usage_cost_usd(result.get("usage"))
            if cost > 0:
                store.add_spend(vk_token, cost)
        return JSONResponse(result)
    return JSONResponse(result)
