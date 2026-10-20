"""Tool invoke HTTP routes — session-scoped RPC (MCP tools/call parity)."""

from __future__ import annotations

from uuid import uuid4

from fastapi import APIRouter, Depends, Request

from supervisor.api import deps
from supervisor.api.schemas import InvokeToolRequest, InvokeToolResponse
from supervisor.api.session_ops import run_op
from supervisor.temporal.contracts import Op

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
    """Runs at most once per tool_call_id; re-sending one returns its recorded outcome."""
    await deps.require_session_access(tenant_id, session_id, request)
    call_id = body.tool_call_id or uuid4().hex
    out = await run_op(
        request,
        session_id,
        tenant_id,
        Op.INVOKE_TOOL,
        {"name": body.name, "arguments": body.arguments},
        op_id=call_id,
    )
    assert out is not None
    return InvokeToolResponse(**out)
