"""Gated AI-plane proxy routes — user JWT required; service JWT used upstream."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status

from prime_agent.config import get_settings
from prime_agent.schemas.ai_health import AiHealthResponse
from prime_agent.schemas.ai_identity import TenantPrincipal
from prime_agent.services.ai_client import AiClient, AiUpstreamError
from prime_agent.web.dependencies import CurrentUser

router = APIRouter()


@router.get("/health", response_model=AiHealthResponse)
async def ai_health(current_user: CurrentUser) -> AiHealthResponse:
    """Proxy supervisor liveness for the authenticated user (D1 vertical)."""
    identity = TenantPrincipal.from_user(current_user)
    client = AiClient.from_settings(get_settings())
    try:
        detail = await client.healthz(identity)
    except AiUpstreamError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=exc.to_dict()["message"],
        ) from exc
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc

    status_value = str(detail.get("status") or detail.get("ok") or "ok")
    return AiHealthResponse(
        status=status_value,
        tenant_id=identity.tenant_id,
        principal_id=identity.principal_id,
        detail={k: v for k, v in detail.items() if k not in {"token", "authorization"}},
    )
