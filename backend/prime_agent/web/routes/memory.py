"""Authenticated Memory BFF routes — Durable Memory proxy (D7 FULL-PROD).

Owner-scoped privacy: forget / erase_subject bind subject to ``current_user.id``.
Scope mapping documented on ``MemoryService`` / ``schemas.memory``.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query, Response, status

from prime_agent.config import get_settings
from prime_agent.schemas.ai_identity import TenantPrincipal
from prime_agent.schemas.memory import (
    MemoryCreateRequest,
    MemoryDetailResponse,
    MemoryEraseSubjectRequest,
    MemoryEraseSubjectResponse,
    MemoryForgetRequest,
    MemoryForgetResponse,
    MemoryListResponse,
    MemorySearchRequest,
    MemorySearchResponse,
    MemoryStatsResponse,
    MemoryUpdateRequest,
)
from prime_agent.services.memory_service import MemoryService
from prime_agent.web.dependencies import CurrentUser

router = APIRouter()
Page = Annotated[int, Query(ge=1)]
PageSize = Annotated[int, Query(ge=1, le=100)]


def _service() -> MemoryService:
    return MemoryService.from_settings(get_settings())


@router.get("", response_model=MemoryListResponse)
async def list_memory(
    current_user: CurrentUser,
    page: Page = 1,
    page_size: PageSize = 50,
    namespace: str | None = None,
) -> MemoryListResponse:
    """List durable facts for the owner (proxied via /v1/memory/recall)."""
    identity = TenantPrincipal.from_user(current_user)
    return await _service().list_items(
        identity,
        page=page,
        page_size=page_size,
        namespace=namespace,
    )


@router.post("/search", response_model=MemorySearchResponse)
async def search_memory(
    body: MemorySearchRequest,
    current_user: CurrentUser,
) -> MemorySearchResponse:
    """Semantic search within the owner Scope (POST /v1/memory/recall)."""
    identity = TenantPrincipal.from_user(current_user)
    return await _service().search(identity, body)


@router.post("/forget", response_model=MemoryForgetResponse)
async def forget_memory(
    body: MemoryForgetRequest,
    current_user: CurrentUser,
) -> MemoryForgetResponse:
    """Soft-forget an item or scope — owner subject forced from current_user."""
    identity = TenantPrincipal.from_user(current_user)
    return await _service().forget(identity, body)


@router.post("/erase_subject", response_model=MemoryEraseSubjectResponse)
async def erase_subject(
    body: MemoryEraseSubjectRequest,
    current_user: CurrentUser,
) -> MemoryEraseSubjectResponse:
    """GDPR-shaped erase for the authenticated subject's durable bank."""
    identity = TenantPrincipal.from_user(current_user)
    return await _service().erase_subject(identity, body)


@router.post("/erase", response_model=MemoryEraseSubjectResponse)
async def erase_memory(
    body: MemoryEraseSubjectRequest,
    current_user: CurrentUser,
) -> MemoryEraseSubjectResponse:
    """Alias of erase_subject for product privacy UX."""
    identity = TenantPrincipal.from_user(current_user)
    return await _service().erase_subject(identity, body)


@router.get("/stats", response_model=MemoryStatsResponse)
async def memory_stats(current_user: CurrentUser) -> MemoryStatsResponse:
    identity = TenantPrincipal.from_user(current_user)
    return await _service().stats(identity)


@router.post("", response_model=MemoryDetailResponse, status_code=status.HTTP_201_CREATED)
async def create_memory(
    body: MemoryCreateRequest,
    current_user: CurrentUser,
) -> MemoryDetailResponse:
    identity = TenantPrincipal.from_user(current_user)
    return await _service().create(identity, body)


@router.get("/{item_id}", response_model=MemoryDetailResponse)
async def get_memory(
    item_id: str,
    current_user: CurrentUser,
) -> MemoryDetailResponse:
    identity = TenantPrincipal.from_user(current_user)
    return await _service().get_item(identity, item_id)


@router.patch("/{item_id}", response_model=MemoryDetailResponse)
async def update_memory(
    item_id: str,
    body: MemoryUpdateRequest,
    current_user: CurrentUser,
) -> MemoryDetailResponse:
    identity = TenantPrincipal.from_user(current_user)
    return await _service().update(identity, item_id, body)


@router.delete("/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_memory(
    item_id: str,
    current_user: CurrentUser,
) -> Response:
    identity = TenantPrincipal.from_user(current_user)
    await _service().delete(identity, item_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
