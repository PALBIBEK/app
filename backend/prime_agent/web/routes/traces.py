"""Authenticated Trace API — Jaeger query proxy or tenant-filtered stub (D13 §5 C)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, status

from prime_agent.config import get_settings
from prime_agent.schemas.ai_identity import TenantPrincipal
from prime_agent.services.ai_client import AiClient, AiUpstreamError
from prime_agent.web.dependencies import CurrentUser

router = APIRouter()


def _client() -> AiClient:
    return AiClient.from_settings(get_settings())


@router.get("")
async def list_traces(current_user: CurrentUser) -> dict[str, Any]:
    """GET /traces — tenant-scoped trace list (Jaeger proxy or empty stub)."""
    identity = TenantPrincipal.from_user(current_user)
    try:
        traces = await _client().list_traces(identity)
    except AiUpstreamError as exc:
        if exc.status_code in (401, 403, 404):
            raise HTTPException(
                status_code=exc.status_code or status.HTTP_404_NOT_FOUND,
                detail="trace not found",
            ) from exc
        # Unreachable Jaeger → tenant-empty stub (do not 503-leak for MVP gate).
        traces = []
    if not traces:
        # T7: no cross-tenant leak; empty tenant looks like not found.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="trace not found",
        )
    return {"traces": traces, "tenant_id": identity.tenant_id}


@router.get("/{trace_id}/spans")
async def get_trace_spans(
    trace_id: str,
    current_user: CurrentUser,
) -> dict[str, Any]:
    """GET /traces/{trace_id}/spans — tenant-scoped span tree."""
    identity = TenantPrincipal.from_user(current_user)
    try:
        spans = await _client().get_trace_spans(identity, trace_id)
    except AiUpstreamError as exc:
        code = exc.status_code if exc.status_code in (401, 403, 404) else 404
        raise HTTPException(
            status_code=code or status.HTTP_404_NOT_FOUND,
            detail="trace not found",
        ) from exc
    if not spans:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="trace not found",
        )
    return {
        "trace_id": trace_id,
        "spans": spans,
        "tenant_id": identity.tenant_id,
    }
