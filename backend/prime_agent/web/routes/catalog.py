"""What a harness can be built from, as the supervisor offers it now."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from prime_agent.config import get_settings
from prime_agent.schemas.ai_identity import TenantPrincipal
from prime_agent.services.ai_client import AiClient, AiUpstreamError
from prime_agent.web.dependencies import CurrentUser

router = APIRouter()


async def _call(coro: Any) -> Any:
    try:
        return await coro
    except AiUpstreamError as exc:
        raise HTTPException(status_code=exc.status_code or 503, detail=exc.to_dict()) from exc


@router.get("/tools")
async def list_tools(current_user: CurrentUser) -> list[dict[str, Any]]:
    """The tools the supervisor can run: name, description, recovery class, parameters."""
    client = AiClient.from_settings(get_settings())
    return await _call(client.list_tools(TenantPrincipal.from_user(current_user)))


@router.get("/models")
async def list_models(current_user: CurrentUser) -> list[dict[str, Any]]:
    """The models the gateway serves (id, label, limits, prices); never an endpoint or a key."""
    client = AiClient.from_settings(get_settings())
    return await _call(client.list_models(TenantPrincipal.from_user(current_user)))
