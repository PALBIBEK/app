"""A session's subagents, as a person reads them (``supervisor.session.subagent_view``).

Subagents are sessions an agent starts with its own tool calls (``supervisor.session.children``);
nothing here starts one. Each is a session of its own, so its transcript, its runs and its
trace are read with the session routes under its id, and a person stops one with
``POST /v1/sessions/{id}/abort`` and messages a continuable one with
``POST /v1/sessions/{id}/messages`` (``supervisor.api.sessions``).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request

from supervisor.api import deps
from supervisor.api.schemas import SubagentListOut, SubagentOut
from supervisor.session.subagent_view import SubagentView

router = APIRouter(tags=["subagents"])


def _out(view: SubagentView) -> SubagentOut:
    return SubagentOut(
        id=view.id,
        parent_id=view.parent_id,
        depth=view.depth,
        label=view.label,
        task=view.task,
        kind=view.kind,
        mode=view.mode,
        status=view.status,
        outcome=view.outcome,
        answer=view.answer,
        answer_truncated=view.answer_truncated,
        open=view.open,
        call_id=view.call_id,
        cost_usd=view.cost_usd,
        created_at=view.created_at,
    )


@router.get("/v1/sessions/{session_id}/subagents", response_model=SubagentListOut)
async def list_subagents(
    session_id: str,
    request: Request,
    tenant_id: str = Depends(deps.current_tenant),
) -> SubagentListOut:
    """Every subagent below the session (its own, theirs, and so on), each level after the
    one above it: what it was asked, how it is doing, what it answered and what it cost."""
    await deps.require_session_access(tenant_id, session_id, request)
    views = await deps.get_manager(request).subagents(session_id)
    return SubagentListOut(subagents=[_out(v) for v in views])


@router.get("/v1/sessions/{session_id}/subagents/{subagent_id}", response_model=SubagentOut)
async def get_subagent(
    session_id: str,
    subagent_id: str,
    request: Request,
    tenant_id: str = Depends(deps.current_tenant),
) -> SubagentOut:
    """One subagent below the session; 404 when ``subagent_id`` is not one."""
    await deps.require_session_access(tenant_id, session_id, request)
    views = await deps.get_manager(request).subagents(session_id, only=subagent_id)
    if not views:
        raise HTTPException(status_code=404, detail="subagent not found")
    return _out(views[0])
