"""Run traces: the user's runs, one session's runs, one run's span tree, one span's content.

Every route answers only for the user's own sessions; anything else is a 404.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query, status

from prime_agent.config import get_settings
from prime_agent.schemas.ai_identity import TenantPrincipal
from prime_agent.schemas.trace import RunTraceResponse, SessionTracesResponse, TraceListResponse
from prime_agent.services.trace_service import TraceNotFoundError, TraceService
from prime_agent.web.dependencies import CurrentUser, DbSession

router = APIRouter()

_RUN_STATUSES = frozenset(
    {"running", "awaiting_approval", "ok", "error", "cancelled", "budget_exceeded", "interrupted"}
)


def _service() -> TraceService:
    return TraceService.from_settings(get_settings())


def _not_found() -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="trace not found")


@router.get("", response_model=TraceListResponse)
async def list_traces(
    db: DbSession,
    current_user: CurrentUser,
    run_status: Annotated[list[str], Query(alias="status")] = [],  # noqa: B006 - FastAPI query list
    agent_id: str | None = None,
    errors: bool = False,
    denied: bool = False,
    q: Annotated[str | None, Query(max_length=200)] = None,
    since_ms: int | None = None,
    before_ms: int | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> TraceListResponse:
    """The user's runs, newest first; ``before_ms`` takes the previous page's ``next_before_ms``."""
    if unknown := set(run_status) - _RUN_STATUSES:
        raise HTTPException(status_code=422, detail=f"unknown status: {', '.join(sorted(unknown))}")
    return await _service().list_runs(
        identity=TenantPrincipal.from_user(current_user),
        user_id=current_user.id,
        db=db,
        statuses=run_status,
        agent_id=agent_id,
        errors=errors,
        denied=denied,
        q=q.strip() if q and q.strip() else None,
        since_ms=since_ms,
        before_ms=before_ms,
        limit=limit,
    )


@router.get("/sessions/{session_id}", response_model=SessionTracesResponse)
async def session_traces(session_id: str, db: DbSession, current_user: CurrentUser) -> SessionTracesResponse:
    """Every run of one session, oldest first."""
    try:
        return await _service().session_runs(
            identity=TenantPrincipal.from_user(current_user), user_id=current_user.id, session_id=session_id, db=db
        )
    except TraceNotFoundError as exc:
        raise _not_found() from exc


@router.get("/sessions/{session_id}/runs/{run_id}", response_model=RunTraceResponse)
async def run_trace(session_id: str, run_id: str, db: DbSession, current_user: CurrentUser) -> RunTraceResponse:
    """One run's spans and rollup (span content comes from the span route)."""
    try:
        return await _service().run(
            identity=TenantPrincipal.from_user(current_user),
            user_id=current_user.id,
            session_id=session_id,
            run_id=run_id,
            db=db,
        )
    except TraceNotFoundError as exc:
        raise _not_found() from exc


@router.get("/sessions/{session_id}/runs/{run_id}/spans/{span_id}")
async def trace_span(
    session_id: str, run_id: str, span_id: str, db: DbSession, current_user: CurrentUser
) -> dict[str, Any]:
    """One span with its full content: messages, arguments, results, the change it made."""
    try:
        return await _service().span(
            identity=TenantPrincipal.from_user(current_user),
            user_id=current_user.id,
            session_id=session_id,
            run_id=run_id,
            span_id=span_id,
            db=db,
        )
    except TraceNotFoundError as exc:
        raise _not_found() from exc


@router.get("/sessions/{session_id}/runs/{run_id}/events")
async def run_events(session_id: str, run_id: str, db: DbSession, current_user: CurrentUser) -> dict[str, Any]:
    """The journal events the run's trace was built from (the trace view's Raw events)."""
    try:
        return await _service().events(
            identity=TenantPrincipal.from_user(current_user),
            user_id=current_user.id,
            session_id=session_id,
            run_id=run_id,
            db=db,
        )
    except TraceNotFoundError as exc:
        raise _not_found() from exc
