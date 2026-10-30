"""Authenticated session BFF routes (D4 production)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Header, Query, Response, status
from fastapi.responses import JSONResponse, StreamingResponse

from prime_agent.config import get_settings
from prime_agent.schemas.ai_identity import TenantPrincipal
from prime_agent.schemas.session import (
    RUN_TERMINAL,
    CreateSessionFromHarness,
    ForkSessionRequest,
    ForkSessionResponse,
    RunResponse,
    SessionApprovalChoice,
    SessionApprovalOut,
    SessionChildOut,
    SessionListResponse,
    SessionMessageRequest,
    SessionMessageResponse,
    SessionPinsResponse,
    SessionResponse,
    SessionStartupResponse,
    SessionToolInvokeRequest,
    SessionToolInvokeResponse,
    SpawnSubagentRequest,
    SpawnSubagentResponse,
)
from prime_agent.services.ai_client import AiPlatformNotReadyError
from prime_agent.services.harness_bind_service import BindDomainError, raise_bind_http
from prime_agent.services.platform_status_service import raise_ai_not_ready_http
from prime_agent.services.session_service import (
    SessionNotFoundError,
    SessionService,
    raise_session_not_found_http,
)
from prime_agent.web.dependencies import CurrentUser, DbSession

router = APIRouter()
Page = Annotated[int, Query(ge=1)]
PageSize = Annotated[int, Query(ge=1, le=100)]


def _service() -> SessionService:
    return SessionService.from_settings(get_settings())


@router.post("", response_model=SessionResponse, status_code=status.HTTP_201_CREATED)
async def create_session(
    body: CreateSessionFromHarness,
    db: DbSession,
    current_user: CurrentUser,
) -> SessionResponse:
    identity = TenantPrincipal.from_user(current_user)
    try:
        return await _service().create_from_harness_id(
            request=body,
            user_id=current_user.id,
            identity=identity,
            db=db,
        )
    except BindDomainError as exc:
        raise_bind_http(exc)
    except AiPlatformNotReadyError as exc:
        raise_ai_not_ready_http(exc)


@router.get("", response_model=SessionListResponse)
async def list_sessions(
    db: DbSession,
    current_user: CurrentUser,
    page: Page = 1,
    page_size: PageSize = 20,
    harness_id: str | None = None,
) -> SessionListResponse:
    identity = TenantPrincipal.from_user(current_user)
    return await _service().list_sessions(
        user_id=current_user.id,
        identity=identity,
        db=db,
        page=page,
        page_size=page_size,
        harness_id=harness_id,
    )


def _run_answer(run: RunResponse) -> JSONResponse:
    """202 while the run is queued or going, 200 once it has ended (the body is the run)."""
    return JSONResponse(status_code=200 if run.status in RUN_TERMINAL else 202, content=run.model_dump())


@router.get("/{session_id}/runs", response_model=list[RunResponse])
async def list_session_runs(
    session_id: str, db: DbSession, current_user: CurrentUser, limit: int = Query(default=20, ge=1, le=100)
) -> list[RunResponse]:
    """The session's runs, newest first."""
    identity = TenantPrincipal.from_user(current_user)
    try:
        return await _service().list_runs(session_id=session_id, user_id=current_user.id, identity=identity, db=db, limit=limit)
    except SessionNotFoundError as exc:
        raise_session_not_found_http(exc)


@router.get("/{session_id}/runs/{run_id}", response_model=RunResponse, responses={202: {"model": RunResponse}})
async def get_session_run(session_id: str, run_id: str, db: DbSession, current_user: CurrentUser) -> JSONResponse:
    """One run by the id its message got: 202 while queued or going, 200 once ended."""
    identity = TenantPrincipal.from_user(current_user)
    try:
        run = await _service().get_run(session_id=session_id, run_id=run_id, user_id=current_user.id, identity=identity, db=db)
    except SessionNotFoundError as exc:
        raise_session_not_found_http(exc)
    return _run_answer(run)


@router.post("/{session_id}/runs/{run_id}/cancel", response_model=RunResponse, responses={202: {"model": RunResponse}})
async def cancel_session_run(session_id: str, run_id: str, db: DbSession, current_user: CurrentUser) -> JSONResponse:
    """Stop a run (a queued one never runs). Idempotent: an ended run comes back unchanged."""
    identity = TenantPrincipal.from_user(current_user)
    try:
        run = await _service().cancel_run(session_id=session_id, run_id=run_id, user_id=current_user.id, identity=identity, db=db)
    except SessionNotFoundError as exc:
        raise_session_not_found_http(exc)
    return _run_answer(run)


@router.get("/{session_id}/pins", response_model=SessionPinsResponse)
async def get_session_pins(session_id: str, db: DbSession, current_user: CurrentUser) -> SessionPinsResponse:
    """The composition the session runs, part by part (404 when it runs a bare harness)."""
    identity = TenantPrincipal.from_user(current_user)
    try:
        return await _service().pins(session_id=session_id, user_id=current_user.id, identity=identity, db=db)
    except SessionNotFoundError as exc:
        raise_session_not_found_http(exc)


@router.get("/{session_id}/startup", response_model=SessionStartupResponse)
async def get_session_startup(
    session_id: str,
    db: DbSession,
    current_user: CurrentUser,
) -> SessionStartupResponse:
    """Where a new session's start stands (poll this after create until ready or failed)."""
    identity = TenantPrincipal.from_user(current_user)
    try:
        return await _service().startup(
            session_id=session_id, user_id=current_user.id, identity=identity, db=db
        )
    except SessionNotFoundError as exc:
        raise_session_not_found_http(exc)


@router.get("/{session_id}", response_model=SessionResponse)
async def get_session(
    session_id: str,
    db: DbSession,
    current_user: CurrentUser,
) -> SessionResponse:
    identity = TenantPrincipal.from_user(current_user)
    try:
        return await _service().get_session(
            session_id=session_id,
            user_id=current_user.id,
            identity=identity,
            db=db,
        )
    except SessionNotFoundError as exc:
        raise_session_not_found_http(exc)


@router.post(
    "/{session_id}/messages",
    response_model=SessionMessageResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def post_message(
    session_id: str,
    body: SessionMessageRequest,
    db: DbSession,
    current_user: CurrentUser,
) -> SessionMessageResponse:
    identity = TenantPrincipal.from_user(current_user)
    try:
        return await _service().post_message(
            session_id=session_id,
            user_id=current_user.id,
            identity=identity,
            body=body,
            db=db,
        )
    except SessionNotFoundError as exc:
        raise_session_not_found_http(exc)
    except AiPlatformNotReadyError as exc:
        raise_ai_not_ready_http(exc)


@router.get("/{session_id}/events")
async def stream_events(
    session_id: str,
    db: DbSession,
    current_user: CurrentUser,
    last_event_id: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
) -> StreamingResponse:
    identity = TenantPrincipal.from_user(current_user)
    try:
        byte_iter = await _service().stream_events(
            session_id=session_id,
            user_id=current_user.id,
            identity=identity,
            last_event_id=last_event_id,
            db=db,
        )
    except SessionNotFoundError as exc:
        raise_session_not_found_http(exc)

    return StreamingResponse(
        byte_iter,
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/{session_id}/abort")
async def abort_session(
    session_id: str,
    db: DbSession,
    current_user: CurrentUser,
) -> dict[str, str]:
    identity = TenantPrincipal.from_user(current_user)
    try:
        return await _service().abort(
            session_id=session_id,
            user_id=current_user.id,
            identity=identity,
            db=db,
        )
    except SessionNotFoundError as exc:
        raise_session_not_found_http(exc)


@router.post(
    "/{session_id}/tools/invoke",
    response_model=SessionToolInvokeResponse,
)
async def invoke_session_tool(
    session_id: str,
    body: SessionToolInvokeRequest,
    db: DbSession,
    current_user: CurrentUser,
) -> SessionToolInvokeResponse:
    """Authenticated BFF proxy — never expose supervisor URL to the browser (D6 F/G)."""
    identity = TenantPrincipal.from_user(current_user)
    try:
        return await _service().invoke_tool(
            session_id=session_id,
            user_id=current_user.id,
            identity=identity,
            body=body,
            db=db,
        )
    except SessionNotFoundError as exc:
        raise_session_not_found_http(exc)
    except AiPlatformNotReadyError as exc:
        raise_ai_not_ready_http(exc)


@router.get(
    "/{session_id}/approvals",
    response_model=list[SessionApprovalOut],
)
async def list_session_approvals(
    session_id: str,
    db: DbSession,
    current_user: CurrentUser,
) -> list[SessionApprovalOut]:
    """List pending approvals for an owned session via AiClient (D6 E — no browser→supervisor)."""
    identity = TenantPrincipal.from_user(current_user)
    try:
        return await _service().list_approvals(
            session_id=session_id,
            user_id=current_user.id,
            identity=identity,
            db=db,
        )
    except SessionNotFoundError as exc:
        raise_session_not_found_http(exc)


@router.post(
    "/{session_id}/approvals/{approval_id}",
    response_model=SessionApprovalOut,
)
async def respond_session_approval(
    session_id: str,
    approval_id: str,
    body: SessionApprovalChoice,
    db: DbSession,
    current_user: CurrentUser,
) -> SessionApprovalOut:
    """Respond to a pending approval for an owned session (D6 E)."""
    identity = TenantPrincipal.from_user(current_user)
    try:
        return await _service().respond_approval(
            session_id=session_id,
            approval_id=approval_id,
            user_id=current_user.id,
            identity=identity,
            body=body,
            db=db,
        )
    except SessionNotFoundError as exc:
        raise_session_not_found_http(exc)


@router.get(
    "/{session_id}/children",
    response_model=list[SessionChildOut],
)
async def list_session_children(
    session_id: str,
    db: DbSession,
    current_user: CurrentUser,
    scope: Annotated[str, Query()] = "direct",
) -> list[SessionChildOut]:
    """List child subagents for an owned session via AiClient (D12 B — no browser→supervisor)."""
    identity = TenantPrincipal.from_user(current_user)
    try:
        return await _service().list_children(
            session_id=session_id,
            user_id=current_user.id,
            identity=identity,
            db=db,
            scope=scope,
        )
    except SessionNotFoundError as exc:
        raise_session_not_found_http(exc)


@router.post(
    "/{session_id}/subagents/spawn",
    response_model=SpawnSubagentResponse,
)
async def spawn_session_subagent(
    session_id: str,
    body: SpawnSubagentRequest,
    db: DbSession,
    current_user: CurrentUser,
) -> SpawnSubagentResponse:
    """Spawn a child subagent for an owned session via AiClient (D12 B)."""
    identity = TenantPrincipal.from_user(current_user)
    try:
        return await _service().spawn_subagent(
            session_id=session_id,
            user_id=current_user.id,
            identity=identity,
            body=body,
            db=db,
        )
    except SessionNotFoundError as exc:
        raise_session_not_found_http(exc)
    except AiPlatformNotReadyError as exc:
        raise_ai_not_ready_http(exc)

@router.post(
    "/{session_id}/fork",
    response_model=ForkSessionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def fork_session(
    session_id: str,
    db: DbSession,
    current_user: CurrentUser,
    body: ForkSessionRequest | None = None,
) -> ForkSessionResponse:
    identity = TenantPrincipal.from_user(current_user)
    try:
        return await _service().fork(
            session_id=session_id,
            user_id=current_user.id,
            identity=identity,
            body=body or ForkSessionRequest(),
            db=db,
        )
    except SessionNotFoundError as exc:
        raise_session_not_found_http(exc)
    except AiPlatformNotReadyError as exc:
        raise_ai_not_ready_http(exc)


@router.delete("/{session_id}", status_code=status.HTTP_204_NO_CONTENT, response_class=Response)
async def terminate_session(
    session_id: str,
    db: DbSession,
    current_user: CurrentUser,
) -> Response:
    identity = TenantPrincipal.from_user(current_user)
    try:
        await _service().terminate(
            session_id=session_id,
            user_id=current_user.id,
            identity=identity,
            db=db,
        )
    except SessionNotFoundError as exc:
        raise_session_not_found_http(exc)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete("/{session_id}/data", status_code=status.HTTP_204_NO_CONTENT, response_class=Response)
async def erase_session(
    session_id: str,
    db: DbSession,
    current_user: CurrentUser,
) -> Response:
    """End the session and erase it for good: its conversation, files and checkpoints."""
    identity = TenantPrincipal.from_user(current_user)
    try:
        await _service().erase(session_id=session_id, user_id=current_user.id, identity=identity, db=db)
    except SessionNotFoundError as exc:
        raise_session_not_found_http(exc)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{session_id}/pause", status_code=status.HTTP_501_NOT_IMPLEMENTED)
async def pause_session(session_id: str, current_user: CurrentUser) -> dict[str, str]:
    return {
        "error_code": "NOT_IMPLEMENTED",
        "message": "pause is not exposed; use abort or wait for sandbox pause",
    }


@router.post("/{session_id}/resume", status_code=status.HTTP_501_NOT_IMPLEMENTED)
async def resume_session(session_id: str, current_user: CurrentUser) -> dict[str, str]:
    return {
        "error_code": "NOT_IMPLEMENTED",
        "message": "resume is not exposed over HTTP",
    }
