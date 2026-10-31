"""Session HTTP routes — translate and delegate to the session's workflow."""

from __future__ import annotations

import asyncio
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from supervisor.api import deps
from supervisor.api.schemas import (
    CreateSessionRequest,
    CreateSessionResponse,
    ForkSessionRequest,
    ForkSessionResponse,
    PostMessageRequest,
    PostMessageResponse,
    SessionDetailResponse,
    SessionSummary,
)
from supervisor.api.session_ops import domain_http_error, run_op
from supervisor.api.workflow_errors import to_http
from supervisor.policy.budget import BudgetExceeded
from supervisor.seams.services import Principal
from supervisor.session import (
    RecoveryBlocked,
    SessionAwaitingApproval,
    SessionNotFound,
    SessionTerminated,
)
from supervisor.temporal.contracts import CreateSession, Message, Op, run_id_for
from supervisor.temporal.sessions import SessionCommandError, TemporalUnavailable

NO_WORKER = (
    "No agent worker is running, so the session cannot start. Start the worker "
    "(./dev.sh starts it; on its own: python -m supervisor worker) and try again."
)


async def _worker_seen(workflows: Any, *, tries: int = 4, gap_s: float = 1.0) -> bool:
    """A worker is polling (re-checked briefly: a worker that just started is not listed yet)."""
    for attempt in range(tries):
        if await workflows.worker_available():
            return True
        if attempt < tries - 1:
            await asyncio.sleep(gap_s)
    return False


class StartupOut(BaseModel):
    """How a session's start stands: ``starting``, ``ready``, ``failed`` (with ``reason``) or
    ``closed``; ``worker_available`` says whether a worker is picking work up at all."""

    session_id: str
    status: str
    worker_available: bool
    reason: str | None = None
    kind: str | None = None
    retrying: str | None = None


router = APIRouter(prefix="/v1/sessions", tags=["sessions"])


@router.post("", status_code=201, response_model=CreateSessionResponse)
async def create_session(
    body: CreateSessionRequest,
    request: Request,
    tenant_id: str = Depends(deps.current_tenant),
    principal: Principal = Depends(deps.current_principal),
    prefer: str | None = Header(default=None),
) -> CreateSessionResponse | JSONResponse:
    """Create a session. With ``Prefer: respond-async`` it answers ``202`` with status
    ``starting`` as soon as the session's workflow is started, and ``GET .../startup`` reports
    how it goes (the production shape for work that runs on workers); without it, it waits
    until the session is ready, but refuses at once when no worker is running."""
    if deps.is_draining(request):
        raise HTTPException(status_code=503, detail="draining")
    manager = deps.get_manager(request)
    if (workflows := deps.get_workflows(request)) is not None:
        session_id = uuid4().hex
        spec = CreateSession(
            tenant_id=tenant_id,
            harness_version=body.harness_version,
            runtime_name=body.runtime,
            composition_id=body.composition_id,
            composition_digest=body.composition_digest,
            scope_id=body.scope_id,
            principal_id=principal.id,
            memory=body.memory.model_dump() if body.memory else None,
        )
        respond_async = "respond-async" in (prefer or "").lower()
        try:
            if respond_async:
                await workflows.start(session_id, spec)
                started = CreateSessionResponse(
                    session_id=session_id,
                    harness_version=body.harness_version,
                    runtime=body.runtime or "",
                    status="starting",
                )
                return JSONResponse(status_code=202, content=started.model_dump(), headers={"Preference-Applied": "respond-async"})
            if not await _worker_seen(workflows):
                raise HTTPException(status_code=503, detail={"reason": "no_worker", "message": NO_WORKER})
            await workflows.create(session_id, spec)
        except (SessionCommandError, TemporalUnavailable) as exc:
            raise to_http(exc) from exc
        info = await manager.get(session_id, tenant_id=tenant_id)
        return CreateSessionResponse(
            session_id=session_id,
            harness_version=body.harness_version,
            runtime=info.runtime if info is not None else body.runtime or "",
            status="active",
        )
    try:
        session_id = await manager.create(
            tenant_id=tenant_id,
            harness_version=body.harness_version,
            runtime_name=body.runtime,
            composition_id=body.composition_id,
            composition_digest=body.composition_digest,
            scope_id=body.scope_id,
            principal_id=principal.id,
            memory=body.memory.model_dump() if body.memory else None,
        )
    except Exception as exc:
        # The same statuses as the workflow path: one table (api.workflow_errors).
        raise domain_http_error(exc) or HTTPException(status_code=503, detail=str(exc)) from exc
    info = await manager.get(session_id, tenant_id=tenant_id)
    return CreateSessionResponse(
        session_id=session_id,
        harness_version=body.harness_version,
        runtime=info.runtime if info is not None else body.runtime or "",
        status="active",
    )


LIST_IDS_MAX = 200
"""The most sessions one list may name (``?ids=``): a page of the product's list."""


@router.get("", response_model=list[SessionSummary])
async def list_sessions(
    request: Request,
    tenant_id: str = Depends(deps.current_tenant),
    ids: list[str] | None = Query(default=None, max_length=LIST_IDS_MAX, description="Only these sessions"),
    include_erased: bool = Query(default=False, description="With ids: name the erased ones too"),
) -> list[SessionSummary]:
    """The caller's sessions, newest first; with ``ids``, only those the caller may see, and
    with ``include_erased`` also each of them that was erased, as status ``erased`` (so a
    client can forget it; one merely not set up yet is simply absent)."""
    manager = deps.get_manager(request)
    rows = await manager.list_sessions(
        tenant_id=tenant_id, principal_id=deps.request_principal_id(request, tenant_id), ids=ids
    )
    erased = await _erased_among(request, tenant_id, ids) if ids and include_erased else []
    return [
        SessionSummary(
            session_id=r.session_id,
            status=r.status,
            harness_version=r.harness_version,
            runtime=r.runtime,
            last_seq=r.last_seq,
            title=r.title,
            message_count=r.message_count,
            total_cost=r.total_cost,
            is_busy=r.is_busy,
        )
        for r in rows
    ] + [
        SessionSummary(session_id=sid, status="erased", harness_version="", runtime="", last_seq=-1)
        for sid in erased
    ]


async def _erased_among(request: Request, tenant_id: str, ids: list[str]) -> list[str]:
    from sqlalchemy import select

    from supervisor.db.models import ErasedSessionRow

    async with deps.get_session_factory(request)() as db:
        found = await db.scalars(
            select(ErasedSessionRow.session_id).where(
                ErasedSessionRow.session_id.in_(ids), ErasedSessionRow.tenant_id == tenant_id
            )
        )
        return sorted(found)


@router.get("/{session_id}", response_model=SessionDetailResponse)
async def get_session(
    session_id: str,
    request: Request,
    tenant_id: str = Depends(deps.current_tenant),
) -> SessionDetailResponse:
    await deps.require_session_access(tenant_id, session_id, request)
    manager = deps.get_manager(request)
    info = await manager.get(session_id, tenant_id=tenant_id)
    assert info is not None
    return SessionDetailResponse(
        session_id=info.session_id,
        status=info.status,
        harness_version=info.harness_version,
        runtime=info.runtime,
        last_seq=info.last_seq,
        title=info.title,
        message_count=info.message_count,
        is_busy=info.is_busy,
        total_cost=info.total_cost,
    )


class PinnedPartOut(BaseModel):
    kind: str
    role: str
    artifact_id: str
    digest: str
    semver: str | None
    status: str
    settings: dict[str, Any]


class SessionPinsOut(BaseModel):
    """What a session runs: its composition and every part, at the exact versions it pinned."""

    composition_id: str
    composition_digest: str
    composition_semver: str | None
    composition_status: str
    harness_version: str
    model_route: str | None
    mcp_configured: bool
    acp_configured: bool
    parts: list[PinnedPartOut]


@router.get("/{session_id}/pins", response_model=SessionPinsOut)
async def get_session_pins(
    session_id: str,
    request: Request,
    tenant_id: str = Depends(deps.current_tenant),
) -> SessionPinsOut:
    """The session's pinned composition, part by part (404 when it pins none: a bare harness)."""
    from dataclasses import asdict

    from supervisor.catalog import SessionPins
    from supervisor.catalog.repository import sync_composition
    from supervisor.catalog.views import pinned_view
    from supervisor.db.models import SessionRow

    await deps.require_session_access(tenant_id, session_id, request)
    factory = deps.get_session_factory(request)
    async with factory() as db:
        row = await db.get(SessionRow, session_id)
    if row is None or not row.composition_pins:
        raise HTTPException(status_code=404, detail="this session runs a harness version, not a composition")
    pins = SessionPins.from_dict(dict(row.composition_pins))
    catalog = deps.get_composition_catalog(request)
    await sync_composition(catalog, factory, pins.composition_id, pins.composition_digest)
    return SessionPinsOut(**asdict(pinned_view(catalog, pins)))


@router.post(
    "/{session_id}/messages",
    status_code=202,
    response_model=PostMessageResponse,
)
async def post_message(
    session_id: str,
    body: PostMessageRequest,
    request: Request,
    tenant_id: str = Depends(deps.current_tenant),
) -> PostMessageResponse:
    if deps.is_draining(request):
        raise HTTPException(status_code=503, detail="draining")
    await deps.require_session_access(tenant_id, session_id, request)
    manager = deps.get_manager(request)

    existing = await deps.recorded_command(request, session_id, body.command_id, body.text)
    if existing is not None:
        return PostMessageResponse(
            turn_id=str(existing["turn_id"]),
            status=str(existing.get("status", "accepted")),
        )

    if (workflows := deps.get_workflows(request)) is not None:
        from supervisor.obs.trace_context import parse_traceparent

        # The workflow prepares the turn first (recovery, budget, approvals), and
        # de-duplicates by command id, so a refused message never claims its id.
        try:
            accepted = await workflows.submit(
                session_id,
                Message(
                    body.command_id,
                    body.text,
                    parse_traceparent(request.headers.get("traceparent")),
                    steer=body.steer,
                ),
            )
        except (SessionCommandError, TemporalUnavailable) as exc:
            raise to_http(exc) from exc
        # The run id the workflow gives this message's run: the client can fetch the run by it.
        _, response = await deps.claim_command(
            request, session_id, body.command_id, body.text,
            turn_id=accepted.run_id if accepted.steered and accepted.run_id else run_id_for(session_id, body.command_id),
        )
        return PostMessageResponse(
            turn_id=str(response["turn_id"]),
            status=str(response.get("status", "accepted")),
            queued=accepted.queued,
            steered=accepted.steered,
            held=accepted.held,
        )

    try:
        # Resolve recovery *before* claiming command_id so a blocked session
        # does not poison the dedup table with a false accept.
        await manager.ensure_ready(session_id)
    except SessionNotFound as exc:
        raise HTTPException(status_code=404, detail="session not found") from exc
    except SessionTerminated as exc:
        raise HTTPException(status_code=409, detail="session terminated") from exc
    except SessionAwaitingApproval as exc:
        raise HTTPException(status_code=409, detail="awaiting approval") from exc
    except BudgetExceeded as exc:
        raise HTTPException(
            status_code=429,
            detail={"reason": "budget_exceeded", "message": str(exc)},
        ) from exc
    except RecoveryBlocked as exc:
        tools = list(exc.tool_call_ids)
        raise HTTPException(
            status_code=409,
            detail={
                "reason": "recovery_blocked",
                "state": str(exc.state),
                "tool_call_ids": tools,
                "message": (
                    f"recovery blocked by unpaired tool call(s): {', '.join(tools)}"
                ),
            },
        ) from exc

    claimed, response = await deps.claim_command(request, session_id, body.command_id, body.text)
    if not claimed:
        return PostMessageResponse(
            turn_id=str(response["turn_id"]),
            status=str(response.get("status", "accepted")),
        )

    try:
        from supervisor.obs.trace_context import parse_traceparent

        inbound = parse_traceparent(request.headers.get("traceparent"))
        await manager.send(
            session_id,
            body.text,
            command_id=body.command_id,
            inbound_trace_id=inbound,
        )
    except SessionNotFound as exc:
        raise HTTPException(status_code=404, detail="session not found") from exc
    except SessionTerminated as exc:
        raise HTTPException(status_code=409, detail="session terminated") from exc
    except SessionAwaitingApproval as exc:
        raise HTTPException(status_code=409, detail="awaiting approval") from exc
    except BudgetExceeded as exc:
        raise HTTPException(
            status_code=429,
            detail={"reason": "budget_exceeded", "message": str(exc)},
        ) from exc
    except RecoveryBlocked as exc:
        tools = list(exc.tool_call_ids)
        raise HTTPException(
            status_code=409,
            detail={
                "reason": "recovery_blocked",
                "state": str(exc.state),
                "tool_call_ids": tools,
                "message": (
                    f"recovery blocked by unpaired tool call(s): {', '.join(tools)}"
                ),
            },
        ) from exc

    return PostMessageResponse(
        turn_id=str(response["turn_id"]),
        status=str(response.get("status", "accepted")),
    )


@router.post("/{session_id}/abort")
async def abort_session(
    session_id: str,
    request: Request,
    tenant_id: str = Depends(deps.current_tenant),
) -> dict[str, str]:
    await deps.require_session_access(tenant_id, session_id, request)
    if (workflows := deps.get_workflows(request)) is not None:
        try:
            await workflows.abort(session_id)
        except (SessionCommandError, TemporalUnavailable) as exc:
            raise to_http(exc) from exc
    else:
        await deps.get_manager(request).abort(session_id)
    return {"status": "ok"}


@router.post("/{session_id}/fork", status_code=201, response_model=ForkSessionResponse)
async def fork_session(
    session_id: str,
    request: Request,
    body: ForkSessionRequest | None = None,
    tenant_id: str = Depends(deps.current_tenant),
) -> ForkSessionResponse:
    if deps.is_draining(request):
        raise HTTPException(status_code=503, detail="draining")
    await deps.require_session_access(tenant_id, session_id, request)
    at_seq = body.at_seq if body is not None else None
    if (workflows := deps.get_workflows(request)) is not None:
        # The fork is a session like any other: it gets its own workflow.
        new_id = uuid4().hex
        try:
            await workflows.fork(session_id, new_id, at_seq=at_seq)
        except (SessionCommandError, TemporalUnavailable) as exc:
            raise to_http(exc) from exc
        return ForkSessionResponse(session_id=new_id)
    try:
        new_id = await deps.get_manager(request).fork(session_id, at_seq=at_seq)
    except Exception as exc:
        if (http := domain_http_error(exc)) is None:
            raise
        raise http from exc
    return ForkSessionResponse(session_id=new_id)


@router.post("/{session_id}/workspace/rehydrate")
async def rehydrate_session_workspace(
    session_id: str,
    request: Request,
    tenant_id: str = Depends(deps.current_tenant),
) -> dict[str, str]:
    """Force rehydrate durable scope files into the session sandbox cache."""
    await deps.require_session_access(tenant_id, session_id, request)
    # The sandbox lives on the session's worker, so that is where this runs.
    await run_op(request, session_id, tenant_id, Op.REHYDRATE_WORKSPACE, {})
    return {"status": "ok"}


@router.delete("/{session_id}", status_code=204)
async def terminate_session(
    session_id: str,
    request: Request,
    tenant_id: str = Depends(deps.current_tenant),
) -> None:
    await deps.require_session_access(tenant_id, session_id, request)
    if (workflows := deps.get_workflows(request)) is not None:
        try:
            await workflows.terminate(session_id)
        except (SessionCommandError, TemporalUnavailable) as exc:
            raise to_http(exc) from exc
    else:
        await deps.get_manager(request).terminate(session_id)


@router.delete("/{session_id}/data", status_code=204)
async def erase_session(
    session_id: str,
    request: Request,
    tenant_id: str = Depends(deps.current_tenant),
) -> None:
    """End the session (if it runs) and erase what the supervisor keeps of it, for good
    (``supervisor.session.erase``). Afterwards the session is not found."""
    await deps.require_session_access(tenant_id, session_id, request)
    await stop_for_erasure(request, session_id)
    await deps.get_manager(request).erase(session_id)


async def stop_for_erasure(request: Request, session_id: str) -> None:
    """End the session, its subagents and its unfinished background jobs, and wait until they
    have: nothing may write what erasing removes. 503 when one is still stopping (erasing
    again retries)."""
    try:
        await end_for_erasure(deps._state(request), session_id)
    except (SessionCommandError, TemporalUnavailable) as exc:
        raise to_http(exc) from exc
    except TimeoutError as exc:
        raise HTTPException(
            status_code=503,
            detail="a session or one of its background jobs is still stopping; erase it again shortly",
        ) from exc


async def end_for_erasure(state: Any, session_id: str) -> None:
    """``stop_for_erasure`` outside a request (the retention sweep): raises as it is."""
    if state.workflows is None:  # in-process mode
        await state.manager.terminate(session_id)
        return
    jobs = await state.reader.unfinished_jobs(session_id)
    # Its subagents end with it (supervisor.session.children); erasing waits for them too.
    subagents = [sid for sid, _ in (await state.manager.session_tree(session_id))[1:]]
    await state.workflows.close(session_id, jobs=jobs, sessions=subagents)


async def end_and_erase(state: Any, session_id: str) -> None:
    """End a session (and its jobs), then erase it: what the retention sweep does to each."""
    await end_for_erasure(state, session_id)
    await state.manager.erase(session_id)


@router.get("/{session_id}/startup", response_model=StartupOut)
async def session_startup(
    session_id: str,
    request: Request,
    tenant_id: str = Depends(deps.current_tenant),
) -> StartupOut:
    """Answered by the Temporal server, so it works while no worker runs (it then says so)."""
    workflows = deps.get_workflows(request)
    if workflows is None:  # in-process mode: a session exists once created
        info = await deps.get_manager(request).get(session_id, tenant_id=tenant_id)
        if info is None:
            raise HTTPException(status_code=404, detail="session not found")
        return StartupOut(session_id=session_id, status="ready", worker_available=True)
    try:
        found = await workflows.startup(session_id)
    except TemporalUnavailable as exc:
        raise to_http(exc) from exc
    caller = deps.request_principal_id(request, tenant_id)
    if found is None:
        raise HTTPException(status_code=404, detail="session not found")
    startup, memo = found
    owner = str(memo.get("principal_id") or "")
    if memo.get("tenant_id") != tenant_id or (owner and caller not in (owner, tenant_id)):
        raise HTTPException(status_code=404, detail="session not found")
    reason = startup.reason
    if startup.status == "starting" and not startup.worker_available:
        reason = NO_WORKER
    elif startup.status == "starting" and startup.retrying:
        reason = f"Starting the session keeps failing and is being retried: {startup.retrying}"
    return StartupOut(
        session_id=session_id, status=startup.status, worker_available=startup.worker_available,
        reason=reason, kind=startup.kind, retrying=startup.retrying,
    )
