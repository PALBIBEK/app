"""Tool invoke HTTP routes — session-scoped RPC (MCP tools/call parity)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request

from supervisor.api import deps
from supervisor.api.schemas import InvokeToolRequest, InvokeToolResponse
from supervisor.seams.services import WorkspaceNotReadyError
from supervisor.session import (
    SessionAwaitingApproval,
    SessionNotFound,
    SessionTerminated,
    ToolInvokeNotConfigured,
)

router = APIRouter(tags=["tools"])


@router.post(
    "/v1/sessions/{session_id}/tools/invoke",
    response_model=InvokeToolResponse,
)
async def invoke_tool(
    session_id: str,
    body: InvokeToolRequest,
    request: Request,
    tenant_id: str = Depends(deps.current_tenant),
) -> InvokeToolResponse:
    await deps.require_session_access(tenant_id, session_id, request)
    manager = deps.get_manager(request)
    try:
        outcome = await manager.invoke_tool(
            session_id,
            tenant_id=tenant_id,
            name=body.name,
            arguments=body.arguments,
            tool_call_id=body.tool_call_id,
        )
    except SessionNotFound as exc:
        raise HTTPException(status_code=404, detail="session not found") from exc
    except SessionTerminated as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except SessionAwaitingApproval as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ToolInvokeNotConfigured as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except WorkspaceNotReadyError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    result = outcome.result
    return InvokeToolResponse(
        tool_call_id=outcome.tool_call_id,
        ok=result.ok,
        payload=result.payload,
        error=result.error,
        code=result.code,
    )
