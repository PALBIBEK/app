"""Models: what a session can select, and the browse runner's model endpoint.

``GET /v1/models`` lists the selectable models — the gateway's discovered groups
and the native models it routes — with what a picker needs to show (protocol,
limits, prices, reasoning levels). Never an endpoint or a key.

``POST /v1/browser-model/chat/completions`` is authenticated by the turn's browser
capability, never by a model key. Every request is re-checked against the
session: its posture, its tenant, and that it still selects the model the
capability names (a changed selection needs a new turn). The call itself goes out
on the session's own gateway key.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import JSONResponse

from supervisor.api.deps import _extract_bearer, _state, current_tenant
from supervisor.models import ModelSpec, model_router
from supervisor.models.browser import (
    ACCOUNT_CHANGED,
    MAX_BODY_BYTES,
    BrowserCompletionError,
    complete_for_session,
    validate_request,
)
from supervisor.models.registry import REGISTRY_BY_ID
from supervisor.session.manager import SessionManager, SessionNotFound

router = APIRouter(tags=["models"])


def model_metadata(spec: ModelSpec) -> dict[str, Any]:
    """A model as a picker shows it: no endpoint, header or key."""
    entry = REGISTRY_BY_ID.get(spec.id)
    pricing = spec.pricing
    return {
        "id": spec.id,
        "name": spec.name,
        "label": entry.button_label if entry is not None and entry.button_label else spec.name,
        "provider": spec.provider,
        "api": spec.api.value,
        "reasoning": spec.reasoning,
        "input": sorted(spec.input),
        "context_window": spec.context_window,
        "max_tokens": spec.max_tokens,
        "cost": {
            "input": pricing.input,
            "output": pricing.output,
            "cache_read": pricing.cache_read,
            "cache_write": pricing.cache_write,
            "tiers": [
                {
                    "input_tokens_above": t.input_tokens_above,
                    "input": t.input,
                    "output": t.output,
                    "cache_read": t.cache_read,
                    "cache_write": t.cache_write,
                }
                for t in pricing.tiers
            ],
        },
        "thinking_levels": list(spec.supported_thinking_levels()),
        "fast_mode": bool(entry and entry.fast_mode),
    }


@router.get("/v1/models")
async def list_models(_tenant: str = Depends(current_tenant)) -> dict[str, Any]:
    """Selectable models; empty without a routed model gateway."""
    router_ = model_router()
    if router_ is None:
        return {"models": []}
    specs = await asyncio.to_thread(router_.selectable_models)
    return {"models": [model_metadata(spec) for spec in specs]}


def _error(status: int, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": message})


async def _body(request: Request) -> bytes | None:
    """The request body, or None once it exceeds the screenshot-history limit."""
    declared = request.headers.get("content-length")
    if declared is not None and declared.isdigit() and int(declared) > MAX_BODY_BYTES:
        return None
    raw = bytearray()
    async for chunk in request.stream():
        raw += chunk
        if len(raw) > MAX_BODY_BYTES:
            return None
    return bytes(raw)


@router.post("/v1/browser-model/chat/completions")
async def browser_model_completion(
    request: Request, authorization: str | None = Header(default=None)
) -> JSONResponse:
    manager: SessionManager = _state(request).manager
    browser = manager.browser
    token = _extract_bearer(authorization)
    grant = browser.capability.verify(token) if browser is not None and token else None
    if grant is None:
        return _error(403, "browser model grant required")
    try:
        row = await manager._get_session_row(grant.session_id)
    except SessionNotFound:
        return _error(409, ACCOUNT_CHANGED)
    if row.tenant_id != grant.tenant_id:
        return _error(403, "browser model grant required")
    if manager.strict_posture(row):
        return _error(403, "browser model requests are disabled in strict posture")
    raw = await _body(request)
    if raw is None:
        return _error(413, "browser model request too large")
    try:
        body = validate_request(json.loads(raw), grant)
    except (ValueError, BrowserCompletionError):
        return _error(400, "invalid browser model request")
    if row.status == "terminated" or manager.session_model(row) != grant.model:
        return _error(409, ACCOUNT_CHANGED)
    client = manager.session_model_client(grant.session_id, row)
    try:
        result = await asyncio.to_thread(complete_for_session, grant, client, body)
    except BrowserCompletionError as exc:
        return _error(exc.status, str(exc))
    return JSONResponse(result)
