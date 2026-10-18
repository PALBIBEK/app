"""Subagent HTTP routes — session-scoped child commands (AGENT_CONTRACT).

Commands run on the worker holding the parent session (its child agents and
background jobs live there); listing children is a read of recorded events.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query, Request

from supervisor.api import deps
from supervisor.api.schemas import (
    BackgroundJobResponse,
    ChildMessageRequest,
    ChildRowOut,
    ContinuableChildResponse,
    SpawnChildResponse,
    SubagentCommandRequest,
)
from supervisor.api.session_ops import domain_http_error, run_op
from supervisor.temporal.contracts import Op

router = APIRouter(tags=["subagents"])


async def _command(
    request: Request, session_id: str, tenant_id: str, kind: Op, body: SubagentCommandRequest
) -> dict[str, Any]:
    await deps.require_session_access(tenant_id, session_id, request)
    out = await run_op(request, session_id, tenant_id, kind, body.model_dump())
    assert out is not None
    return out


@router.post(
    "/v1/sessions/{session_id}/subagents/spawn",
    response_model=SpawnChildResponse,
)
async def spawn_child(
    session_id: str,
    body: SubagentCommandRequest,
    request: Request,
    tenant_id: str = Depends(deps.current_tenant),
) -> SpawnChildResponse:
    out = await _command(request, session_id, tenant_id, Op.SPAWN_CHILD, body)
    return SpawnChildResponse(**out)


@router.post(
    "/v1/sessions/{session_id}/subagents/fork",
    response_model=SpawnChildResponse,
)
async def fork_child(
    session_id: str,
    body: SubagentCommandRequest,
    request: Request,
    tenant_id: str = Depends(deps.current_tenant),
) -> SpawnChildResponse:
    out = await _command(request, session_id, tenant_id, Op.FORK_CHILD, body)
    return SpawnChildResponse(**out)


@router.post(
    "/v1/sessions/{session_id}/subagents/background",
    response_model=BackgroundJobResponse,
)
async def start_background(
    session_id: str,
    body: SubagentCommandRequest,
    request: Request,
    tenant_id: str = Depends(deps.current_tenant),
) -> BackgroundJobResponse:
    out = await _command(request, session_id, tenant_id, Op.START_BACKGROUND, body)
    return BackgroundJobResponse(**out)


@router.post(
    "/v1/sessions/{session_id}/subagents/continuable",
    response_model=ContinuableChildResponse,
)
async def start_continuable(
    session_id: str,
    body: SubagentCommandRequest,
    request: Request,
    tenant_id: str = Depends(deps.current_tenant),
) -> ContinuableChildResponse:
    out = await _command(request, session_id, tenant_id, Op.START_CONTINUABLE, body)
    return ContinuableChildResponse(**out)


@router.post("/v1/sessions/{session_id}/children/{child_id}/message", status_code=204)
async def send_child_message(
    session_id: str,
    child_id: str,
    body: ChildMessageRequest,
    request: Request,
    tenant_id: str = Depends(deps.current_tenant),
) -> None:
    await deps.require_session_access(tenant_id, session_id, request)
    await run_op(
        request,
        session_id,
        tenant_id,
        Op.SEND_CHILD_MESSAGE,
        {"child_id": child_id, "text": body.text},
    )


@router.post("/v1/sessions/{session_id}/children/{child_id}/interrupt", status_code=204)
async def interrupt_child(
    session_id: str,
    child_id: str,
    request: Request,
    tenant_id: str = Depends(deps.current_tenant),
) -> None:
    await deps.require_session_access(tenant_id, session_id, request)
    await run_op(request, session_id, tenant_id, Op.INTERRUPT_CHILD, {"child_id": child_id})


@router.get(
    "/v1/sessions/{session_id}/children",
    response_model=list[ChildRowOut],
)
async def list_children(
    session_id: str,
    request: Request,
    tenant_id: str = Depends(deps.current_tenant),
    scope: str = Query(default="direct"),
) -> list[ChildRowOut]:
    await deps.require_session_access(tenant_id, session_id, request)
    try:
        rows = await deps.get_manager(request).list_children(
            session_id, tenant_id=tenant_id, scope=scope
        )
    except Exception as exc:
        if (http := domain_http_error(exc)) is None:
            raise
        raise http from exc
    return [
        ChildRowOut(
            id=r.id,
            mode=r.mode,
            provider=r.provider,
            parent_id=r.parent_id,
            run_id=r.run_id,
            label=r.label,
            activity=r.activity,
            depth=r.depth,
        )
        for r in rows
    ]
