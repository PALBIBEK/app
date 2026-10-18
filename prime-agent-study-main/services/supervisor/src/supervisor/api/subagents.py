"""Subagent HTTP routes — session-scoped child commands (AGENT_CONTRACT)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from supervisor.api import deps
from supervisor.api.schemas import (
    BackgroundJobResponse,
    ChildMessageRequest,
    ChildRowOut,
    ContinuableChildResponse,
    SpawnChildResponse,
    SubagentCommandRequest,
)
from supervisor.seams import (
    BadArgs,
    DepthExceeded,
    ProviderMissing,
    SeamError,
    SubagentRequest,
    UnsupportedCapability,
)
from supervisor.session import (
    SessionAwaitingApproval,
    SessionNotFound,
    SessionTerminated,
    SubagentNotConfigured,
)

router = APIRouter(tags=["subagents"])


def _to_request(body: SubagentCommandRequest) -> SubagentRequest:
    return SubagentRequest(
        prompt=body.prompt,
        description=body.description,
        parent_depth=body.parent_depth,
        mode=body.mode,
        run_id=body.run_id,
        tool_filter=body.tool_filter,
        persona=body.persona,
        depth_limit=body.depth_limit,
        output_schema=body.output_schema,
    )


def _session_errors(exc: Exception) -> HTTPException:
    if isinstance(exc, SessionNotFound):
        return HTTPException(status_code=404, detail="session not found")
    if isinstance(exc, (SessionTerminated, SessionAwaitingApproval)):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, SubagentNotConfigured):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, DepthExceeded):
        return HTTPException(
            status_code=400,
            detail={"code": exc.code, "message": str(exc)},
        )
    if isinstance(exc, BadArgs):
        return HTTPException(status_code=400, detail=str(exc))
    if isinstance(exc, ProviderMissing):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, UnsupportedCapability):
        return HTTPException(status_code=400, detail=str(exc))
    if isinstance(exc, SeamError):
        return HTTPException(status_code=400, detail=str(exc))
    raise exc


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
    await deps.require_session_access(tenant_id, session_id, request)
    manager = deps.get_manager(request)
    try:
        result = await manager.spawn_child(
            session_id,
            tenant_id=tenant_id,
            provider=body.provider,
            request=_to_request(body),
        )
    except Exception as exc:
        raise _session_errors(exc) from exc
    return SpawnChildResponse(
        child_session_id=result.child_id or "",
        text=result.text or None,
        status=result.status,
    )


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
    await deps.require_session_access(tenant_id, session_id, request)
    manager = deps.get_manager(request)
    try:
        result = await manager.fork_child(
            session_id,
            tenant_id=tenant_id,
            provider=body.provider,
            request=_to_request(body),
        )
    except Exception as exc:
        raise _session_errors(exc) from exc
    return SpawnChildResponse(
        child_session_id=result.child_id or "",
        text=result.text or None,
        status=result.status,
    )


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
    await deps.require_session_access(tenant_id, session_id, request)
    manager = deps.get_manager(request)
    try:
        handle = await manager.start_subagent_background(
            session_id,
            tenant_id=tenant_id,
            provider=body.provider,
            request=_to_request(body),
        )
    except Exception as exc:
        raise _session_errors(exc) from exc
    return BackgroundJobResponse(job_id=handle.job_id)


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
    await deps.require_session_access(tenant_id, session_id, request)
    manager = deps.get_manager(request)
    try:
        handle = await manager.start_subagent_continuable(
            session_id,
            tenant_id=tenant_id,
            provider=body.provider,
            request=_to_request(body),
        )
    except Exception as exc:
        raise _session_errors(exc) from exc
    return ContinuableChildResponse(
        child_id=handle.child_id,
        message_id=handle.message_id or None,
    )


@router.post("/v1/sessions/{session_id}/children/{child_id}/message", status_code=204)
async def send_child_message(
    session_id: str,
    child_id: str,
    body: ChildMessageRequest,
    request: Request,
    tenant_id: str = Depends(deps.current_tenant),
) -> None:
    await deps.require_session_access(tenant_id, session_id, request)
    manager = deps.get_manager(request)
    try:
        await manager.send_child_message(
            session_id,
            tenant_id=tenant_id,
            child_id=child_id,
            text=body.text,
        )
    except Exception as exc:
        raise _session_errors(exc) from exc


@router.post("/v1/sessions/{session_id}/children/{child_id}/interrupt", status_code=204)
async def interrupt_child(
    session_id: str,
    child_id: str,
    request: Request,
    tenant_id: str = Depends(deps.current_tenant),
) -> None:
    await deps.require_session_access(tenant_id, session_id, request)
    manager = deps.get_manager(request)
    try:
        await manager.interrupt_child(
            session_id,
            tenant_id=tenant_id,
            child_id=child_id,
        )
    except Exception as exc:
        raise _session_errors(exc) from exc


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
    manager = deps.get_manager(request)
    try:
        rows = await manager.list_children(
            session_id,
            tenant_id=tenant_id,
            scope=scope,
        )
    except Exception as exc:
        raise _session_errors(exc) from exc
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
