"""Model providers: which vendors the gateway can call, and setting or removing their keys.

The keys are the gateway's (``supervisor.gateway.vendor_keys``): this API passes a key to it
once, on the supervisor's gateway key, and never stores, logs or returns one. Who may change
them is the caller's decision (the product's admins); this API takes the principal from the
verified service identity and records it with the change. A change re-reads the gateway's
models at the next listing, so a vendor's models appear or go at once.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from supervisor.api import deps
from supervisor.models import ModelRouter, model_router

router = APIRouter(prefix="/v1/model-providers", tags=["models"])

_TIMEOUT_S = 15.0
"""A key is checked with its vendor (five seconds) before the gateway answers."""


class ProviderKeyIn(BaseModel):
    key: str = Field(min_length=1, max_length=4096)


def _router() -> ModelRouter:
    routed = model_router()
    if routed is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "NO_GATEWAY", "message": "no model gateway is configured"},
        )
    return routed


def call_gateway(routed: ModelRouter, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
    """One call to the gateway on the supervisor's key; its error passes through as is."""
    config = routed.config
    try:
        with routed.http_client() as client:
            response = client.request(
                method,
                f"{config.root}{path}",
                headers={config.api_key_header: config.api_key},
                timeout=_TIMEOUT_S,
                **kwargs,
            )
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=502, detail={"code": "GATEWAY_UNREACHABLE", "message": type(exc).__name__}
        ) from None
    try:
        body: Any = response.json()
    except ValueError:
        body = None
    if not response.is_success:
        error = (body or {}).get("detail", {}).get("error", {}) if isinstance(body, dict) else {}
        raise HTTPException(
            status_code=response.status_code,
            detail={
                "code": str(error.get("code") or "GATEWAY_ERROR"),
                "message": str(
                    error.get("message")
                    or f"the model gateway answered HTTP {response.status_code}"
                ),
            },
        )
    if not isinstance(body, dict):
        raise HTTPException(
            status_code=502, detail={"code": "GATEWAY_ERROR", "message": "not a JSON object"}
        )
    return body


@router.get("")
async def list_providers(_tenant: str = Depends(deps.current_tenant)) -> dict[str, Any]:
    """Every vendor: whether it has a key and where from, and whether keys can be set."""
    return await asyncio.to_thread(call_gateway, _router(), "GET", "/v1/vendors")


@router.put("/{vendor}/key")
async def set_provider_key(
    vendor: str, body: ProviderKeyIn, request: Request, tenant: str = Depends(deps.current_tenant)
) -> dict[str, Any]:
    """Check the key with the vendor and keep it at the gateway."""
    routed = _router()
    payload = {"key": body.key, "updated_by": deps.request_principal_id(request, tenant)}
    result = await asyncio.to_thread(
        call_gateway, routed, "PUT", f"/v1/vendors/{vendor}/key", json=payload
    )
    routed.catalog.expire()
    return result


@router.delete("/{vendor}/key")
async def remove_provider_key(
    vendor: str, request: Request, tenant: str = Depends(deps.current_tenant)
) -> dict[str, Any]:
    """Remove the vendor's key: it is off from now."""
    routed = _router()
    params = {"updated_by": deps.request_principal_id(request, tenant)}
    result = await asyncio.to_thread(
        call_gateway, routed, "DELETE", f"/v1/vendors/{vendor}/key", params=params
    )
    routed.catalog.expire()
    return result
