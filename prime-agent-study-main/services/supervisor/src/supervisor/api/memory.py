"""Memory HTTP routes — harness-neutral compaction + recall RPC."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request

from supervisor.api import deps
from supervisor.api.schemas import (
    CompactMemoryRequest,
    CompactMemoryResponse,
    RecallMemoryRequest,
    RecallMemoryResponse,
)
from supervisor.seams import CompactionPreparation
from supervisor.session import (
    MemoryNotConfigured,
    SessionAwaitingApproval,
    SessionNotFound,
    SessionTerminated,
)

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
    manager = deps.get_manager(request)
    preparation = CompactionPreparation(
        first_kept_entry_id=body.first_kept_entry_id,
        messages_to_summarize=body.messages_to_summarize,
        tokens_before=body.tokens_before,
        previous_summary=body.previous_summary,
        context_window=body.context_window,
        reserve_tokens=body.reserve_tokens,
    )
    try:
        outcome = await manager.compact_memory(
            session_id,
            tenant_id=tenant_id,
            preparation=preparation,
            reason=body.reason,
        )
    except SessionNotFound as exc:
        raise HTTPException(status_code=404, detail="session not found") from exc
    except SessionTerminated as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except SessionAwaitingApproval as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except MemoryNotConfigured as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    return CompactMemoryResponse(
        compaction_id=outcome.compaction_id,
        stop_reason=outcome.stop_reason,
        summary=outcome.summary,
        tokens_before=outcome.tokens_before,
        tokens_after=outcome.tokens_after,
        summary_digest=outcome.summary_digest,
    )


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
    manager = deps.get_manager(request)
    try:
        outcome = await manager.recall_memory(
            session_id,
            tenant_id=tenant_id,
            user_prompt=body.user_prompt,
        )
    except SessionNotFound as exc:
        raise HTTPException(status_code=404, detail="session not found") from exc
    except SessionTerminated as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except SessionAwaitingApproval as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except MemoryNotConfigured as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    return RecallMemoryResponse(
        block_id=outcome.block_id,
        injected=outcome.injected,
        source=outcome.source,
        text=outcome.text,
        token_estimate=outcome.token_estimate,
    )
