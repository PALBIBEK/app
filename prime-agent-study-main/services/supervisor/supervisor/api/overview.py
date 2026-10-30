"""Views across the caller's sessions: the approvals inbox, sandboxes and usage.

All three are reads. Each shows only the sessions the caller may see (their
own; a tenant-level caller sees the tenant's), like ``GET /v1/sessions``.
"""

from __future__ import annotations

import time
from dataclasses import asdict
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel

from supervisor.api import deps
from supervisor.session.overview import approval_inbox, sandboxes, usage

router = APIRouter(tags=["overview"])

_DAY_MS = 86_400_000
MAX_USAGE_WINDOW_DAYS = 92


class InboxItemOut(BaseModel):
    approval_id: str
    session_id: str
    session_title: str | None = None
    agent: str
    prompt: str
    options: list[str]
    created_at_ms: int | None = None
    expires_at_ms: int | None = None
    root_session_id: str | None = None
    """Set when a subagent's call waits: the session at the top of its tree."""
    subagent_label: str | None = None


class SandboxOut(BaseModel):
    session_id: str
    hand: str
    state: Literal["running", "stopped", "lost", "not_started"]
    sandbox_id: str | None = None
    provider: str | None = None
    since_ms: int | None = None
    cause: str | None = None
    last_checkpoint_ms: int | None = None
    checkpoint_files: int = 0
    processes: list[str] = []
    reason: str | None = None
    logs_ref: str | None = None
    lease_holder: str | None = None
    lease_expires_ms: int | None = None


class UsageRowOut(BaseModel):
    key: str
    input_tokens: int
    output_tokens: int
    total_tokens: int
    cost: float
    model_calls: int
    sessions: int


class UsageOut(BaseModel):
    since_ms: int
    until_ms: int
    by: Literal["agent", "day", "session"]
    rows: list[UsageRowOut]
    total: UsageRowOut


@router.get("/v1/approvals", response_model=list[InboxItemOut])
async def list_approval_inbox(
    request: Request,
    status: Literal["pending"] = Query(default="pending", description="Only pending approvals are listed"),
    limit: int = Query(default=200, ge=1, le=500),
    tenant_id: str = Depends(deps.current_tenant),
) -> list[InboxItemOut]:
    """Every approval waiting for an answer, across the caller's sessions, oldest first."""
    items = await approval_inbox(
        deps.get_session_factory(request),
        tenant_id=tenant_id,
        principal_id=deps.request_principal_id(request, tenant_id),
        limit=limit,
    )
    return [InboxItemOut(**asdict(i)) for i in items]


@router.get("/v1/sandboxes", response_model=list[SandboxOut])
async def list_sandboxes(
    request: Request,
    session_id: str | None = Query(default=None, description="One session's sandboxes; else every open session's"),
    include_ended: bool = Query(default=False, description="Also sessions that have ended"),
    tenant_id: str = Depends(deps.current_tenant),
) -> list[SandboxOut]:
    """Each sandbox's state from the session's events: running, stopped (idle), lost (with its logs), or not started."""
    if session_id is not None:
        await deps.require_session_access(tenant_id, session_id, request)
    views = await sandboxes(
        deps.get_session_factory(request),
        deps.get_reader(request),
        tenant_id=tenant_id,
        principal_id=deps.request_principal_id(request, tenant_id),
        session_id=session_id,
        include_ended=include_ended,
    )
    return [SandboxOut(**asdict(v)) for v in views]


@router.get("/v1/usage", response_model=UsageOut)
async def get_usage(
    request: Request,
    since_ms: int | None = Query(default=None, ge=0, description="Window start (epoch ms); default 30 days ago"),
    until_ms: int | None = Query(default=None, ge=0, description="Window end (epoch ms); default now"),
    by: Literal["agent", "day", "session"] = Query(default="day"),
    tenant_id: str = Depends(deps.current_tenant),
) -> UsageOut:
    """Tokens and cost of every model call in the window, grouped by day, agent or session."""
    until = until_ms if until_ms is not None else int(time.time() * 1000)
    since = since_ms if since_ms is not None else until - 30 * _DAY_MS
    if since >= until:
        raise HTTPException(status_code=422, detail="since_ms must be before until_ms")
    if until - since > MAX_USAGE_WINDOW_DAYS * _DAY_MS:
        raise HTTPException(status_code=422, detail=f"the window is at most {MAX_USAGE_WINDOW_DAYS} days")
    rows, total = await usage(
        deps.get_session_factory(request),
        deps.get_reader(request),
        tenant_id=tenant_id,
        principal_id=deps.request_principal_id(request, tenant_id),
        since_ms=since,
        until_ms=until,
        by=by,
    )
    return UsageOut(
        since_ms=since,
        until_ms=until,
        by=by,
        rows=[UsageRowOut(**r) for r in rows],
        total=UsageRowOut(**total),
    )
