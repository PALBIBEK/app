"""Authenticated product readiness gate (D2)."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status

from prime_agent.config import get_settings
from prime_agent.schemas.ai_identity import TenantPrincipal
from prime_agent.schemas.platform_status import PlatformStatusResponse
from prime_agent.services.ai_client import AiPlatformNotReadyError
from prime_agent.services.platform_status_service import (
    PlatformStatusService,
    raise_ai_not_ready_http,
)
from prime_agent.web.dependencies import CurrentUser

router = APIRouter()


@router.get("/status", response_model=PlatformStatusResponse)
async def platform_status(current_user: CurrentUser) -> PlatformStatusResponse:
    """Return AI plane product readiness for the logged-in user.

    Always 200 when authenticated — including ready:false — so the UI can gate
    Test/send without treating AI outage as an auth failure.
    """
    identity = TenantPrincipal.from_user(current_user)
    service = PlatformStatusService.from_settings(get_settings())
    return await service.get_status(identity)


@router.post("/ensure-ready", response_model=PlatformStatusResponse)
async def ensure_platform_ready(current_user: CurrentUser) -> PlatformStatusResponse:
    """Shared fail-closed helper surface for future AI mutations (D4)."""
    identity = TenantPrincipal.from_user(current_user)
    service = PlatformStatusService.from_settings(get_settings())
    try:
        return await service.ensure_ready(identity)
    except AiPlatformNotReadyError as exc:
        raise_ai_not_ready_http(exc)
        raise  # pragma: no cover
