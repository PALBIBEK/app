"""Approval HTTP routes — thin translate/delegate."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from supervisor.api import deps
from supervisor.policy.approvals import (
    ApprovalError,
    InvalidApprovalChoice,
    UnknownApproval,
)
from supervisor.session import SessionNotFound

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
        resolved = await manager.respond_approval(approval_id, body.choice)
        approval = await broker.get(approval_id)
        assert approval is not None
    except UnknownApproval as exc:
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
        status=resolved.status,
        choice=approval.choice,
        timeout_s=approval.timeout_s,
    )
