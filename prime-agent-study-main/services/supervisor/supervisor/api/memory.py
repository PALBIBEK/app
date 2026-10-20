"""Memory HTTP routes — harness-neutral compaction + recall RPC."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from supervisor.api import deps
from supervisor.api.schemas import (
    CompactMemoryRequest,
    CompactMemoryResponse,
    RecallMemoryRequest,
    RecallMemoryResponse,
)
from supervisor.api.session_ops import run_op
from supervisor.temporal.contracts import Op

router = APIRouter(tags=["memory"])


@router.post(
    "/v1/sessions/{session_id}/memory/compact",
    response_model=CompactMemoryResponse,
)
async def compact_memory(
    session_id: str,
    body: CompactMemoryRequest,
    request: Request,
    tenant_id: str = Depends(deps.current_tenant),
) -> CompactMemoryResponse:
    await deps.require_session_access(tenant_id, session_id, request)
    out = await run_op(request, session_id, tenant_id, Op.COMPACT_MEMORY, body.model_dump())
    assert out is not None
    return CompactMemoryResponse(**out)


@router.post(
    "/v1/sessions/{session_id}/memory/recall",
    response_model=RecallMemoryResponse,
)
async def recall_memory(
    session_id: str,
    body: RecallMemoryRequest,
    request: Request,
    tenant_id: str = Depends(deps.current_tenant),
) -> RecallMemoryResponse:
    await deps.require_session_access(tenant_id, session_id, request)
    out = await run_op(request, session_id, tenant_id, Op.RECALL_MEMORY, body.model_dump())
    assert out is not None
    return RecallMemoryResponse(**out)
