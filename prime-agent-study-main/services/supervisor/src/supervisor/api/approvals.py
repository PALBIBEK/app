"""Approval HTTP routes — thin translate/delegate."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from supervisor.api import deps
from supervisor.api.workflow_errors import to_http
from supervisor.policy.errors import (
    ApprovalError,
    ApprovalNotFound,
    InvalidApprovalChoice,
)
from supervisor.session import SessionNotFound
from supervisor.temporal.sessions import SessionCommandError, TemporalUnavailable

router = APIRouter(tags=["approvals"])


class ApprovalChoice(BaseModel):
    choice: str = Field(min_length=1)


class ApprovalOut(BaseModel):
    approval_id: str
    session_id: str
    prompt: str
    options: list[str]
    status: str
    choice: str | None = None
    timeout_s: float
    responded_by: str | None = None


@router.get("/v1/sessions/{session_id}/approvals", response_model=list[ApprovalOut])
async def list_approvals(
    session_id: str,
    request: Request,
    tenant_id: str = Depends(deps.current_tenant),
) -> list[ApprovalOut]:
    await deps.require_session_access(tenant_id, session_id, request)
    pending = await deps.get_manager(request).pending_approvals(session_id)
    return [
        ApprovalOut(
            approval_id=a.approval_id,
            session_id=a.session_id,
            prompt=a.prompt,
            options=a.options,
            status=a.status,
            choice=a.choice,
            timeout_s=a.timeout_s,
            responded_by=a.responded_by,
        )
        for a in pending
    ]


@router.post("/v1/approvals/{approval_id}", response_model=ApprovalOut)
async def respond_approval(
    approval_id: str,
    body: ApprovalChoice,
    request: Request,
    tenant_id: str = Depends(deps.current_tenant),
) -> ApprovalOut:
    manager = deps.get_manager(request)
    try:
        # Tenant check via approval → session
        from supervisor.policy.approvals import ApprovalBroker

        broker: ApprovalBroker = manager._approvals
        existing = await broker.get(approval_id)
        if existing is None:
            raise HTTPException(status_code=404, detail="approval not found")
        if existing.status == "timed_out":
            raise HTTPException(status_code=409, detail="approval already timed out")
        await deps.require_session_access(tenant_id, existing.session_id, request)
        responder = deps.request_principal_id(request, tenant_id)
        if (workflows := deps.get_workflows(request)) is not None:
            # Delivered through the session's workflow, to the worker holding its runtime.
            try:
                await workflows.respond_approval(
                    existing.session_id, approval_id, body.choice, responded_by=responder
                )
            except (SessionCommandError, TemporalUnavailable) as exc:
                raise to_http(exc) from exc
            approval = await broker.get(approval_id)
            assert approval is not None
            status = approval.status
        else:
            status = (
                await manager.respond_approval(approval_id, body.choice, responded_by=responder)
            ).status
            approval = await broker.get(approval_id)
            assert approval is not None
    except ApprovalNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except InvalidApprovalChoice as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ApprovalError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except SessionNotFound as exc:
        raise HTTPException(status_code=404, detail="session not found") from exc

    return ApprovalOut(
        approval_id=approval.approval_id,
        session_id=approval.session_id,
        prompt=approval.prompt,
        options=approval.options,
        status=status,
        choice=approval.choice,
        timeout_s=approval.timeout_s,
        responded_by=approval.responded_by,
    )
