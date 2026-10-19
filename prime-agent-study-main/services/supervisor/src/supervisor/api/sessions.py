"""Session HTTP routes — translate and delegate to the session's workflow."""

from __future__ import annotations

from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request

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
from supervisor.catalog import HarnessVersionMismatch, NotOffered
from supervisor.harness import HarnessNotOffered, IncompatibleHarness
from supervisor.policy.budget import BudgetExceeded
from supervisor.policy.quotas import QuotaExceeded
from supervisor.seams.services import GrantDenied, NotScopeMember, Principal
from supervisor.session import (
    RecoveryBlocked,
    SessionAwaitingApproval,
    SessionNotFound,
    SessionTerminated,
)
from supervisor.temporal.contracts import CreateSession, Message, Op
from supervisor.temporal.sessions import SessionCommandError, TemporalUnavailable

router = APIRouter(prefix="/v1/sessions", tags=["sessions"])


@router.post("", status_code=201, response_model=CreateSessionResponse)
async def create_session(
    body: CreateSessionRequest,
    request: Request,
    tenant_id: str = Depends(deps.current_tenant),
    principal: Principal = Depends(deps.current_principal),
) -> CreateSessionResponse:
    if deps.is_draining(request):
        raise HTTPException(status_code=503, detail="draining")
    manager = deps.get_manager(request)
    if (workflows := deps.get_workflows(request)) is not None:
        session_id = uuid4().hex
        try:
            await workflows.create(
                session_id,
                CreateSession(
                    tenant_id=tenant_id,
                    harness_version=body.harness_version,
                    runtime_name=body.runtime,
                    composition_id=body.composition_id,
                    composition_digest=body.composition_digest,
                    scope_id=body.scope_id,
                    principal_id=principal.id,
                    memory=body.memory.model_dump() if body.memory else None,
                ),
            )
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
    except IncompatibleHarness as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except HarnessNotOffered as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except NotOffered as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except HarnessVersionMismatch as exc:
        raise HTTPException(status_code=422, detail={"code": exc.code, "message": str(exc)}) from exc
    except QuotaExceeded as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    except (GrantDenied, NotScopeMember) as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    info = await manager.get(session_id, tenant_id=tenant_id)
    return CreateSessionResponse(
        session_id=session_id,
        harness_version=body.harness_version,
        runtime=info.runtime if info is not None else body.runtime or "",
        status="active",
    )


@router.get("", response_model=list[SessionSummary])
async def list_sessions(
    request: Request,
    tenant_id: str = Depends(deps.current_tenant),
) -> list[SessionSummary]:
    manager = deps.get_manager(request)
    rows = await manager.list_sessions(
        tenant_id=tenant_id, principal_id=deps.request_principal_id(request, tenant_id)
    )
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
        )
        for r in rows
    ]


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
    commands = deps.get_commands(request)
    manager = deps.get_manager(request)

    existing = await commands.get(session_id, body.command_id)
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
            await workflows.submit(
                session_id,
                Message(
                    body.command_id,
                    body.text,
                    parse_traceparent(request.headers.get("traceparent")),
                ),
            )
        except (SessionCommandError, TemporalUnavailable) as exc:
            raise to_http(exc) from exc
        _, response = await commands.claim(session_id, body.command_id)
        return PostMessageResponse(
            turn_id=str(response["turn_id"]),
            status=str(response.get("status", "accepted")),
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

    claimed, response = await commands.claim(session_id, body.command_id)
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
