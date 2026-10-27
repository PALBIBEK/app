"""Run traces: the user's runs, one session's runs, one run's span tree, one span's content.

Every route answers only for the user's own sessions; anything else is a 404.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query, Response, status

from prime_agent.config import get_settings
from prime_agent.schemas.ai_identity import TenantPrincipal
from prime_agent.schemas.trace import (
    AgentQualityResponse,
    FeedbackRequest,
    IssuesResponse,
    Judge,
    JudgeRequest,
    JudgesResponse,
    RunTraceResponse,
    SessionTracesResponse,
    TraceListResponse,
    TraceScore,
)
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
    failing_checks: bool = False,
    feedback: Annotated[str | None, Query(pattern="^(good|bad)$")] = None,
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
        failing_checks=failing_checks,
        feedback=feedback,
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


@router.put("/sessions/{session_id}/runs/{run_id}/feedback", response_model=TraceScore)
async def put_feedback(
    session_id: str, run_id: str, body: FeedbackRequest, db: DbSession, current_user: CurrentUser
) -> TraceScore:
    """The user's verdict on the run, or on one of its spans; saying it again replaces it."""
    try:
        score = await _service().feedback(
            identity=TenantPrincipal.from_user(current_user),
            user_id=current_user.id,
            session_id=session_id,
            run_id=run_id,
            body=body,
            span_id=body.span_id,
            db=db,
        )
    except TraceNotFoundError as exc:
        raise _not_found() from exc
    assert score is not None
    return score


@router.delete("/sessions/{session_id}/runs/{run_id}/feedback", status_code=204, response_class=Response)
async def delete_feedback(
    session_id: str, run_id: str, db: DbSession, current_user: CurrentUser, span_id: str | None = None
) -> Response:
    """Take back the user's verdict on the run (or the span)."""
    try:
        await _service().feedback(
            identity=TenantPrincipal.from_user(current_user),
            user_id=current_user.id,
            session_id=session_id,
            run_id=run_id,
            body=None,
            span_id=span_id,
            db=db,
        )
    except TraceNotFoundError as exc:
        raise _not_found() from exc
    return Response(status_code=204)


@router.get("/agents/{agent_id}/versions", response_model=AgentQualityResponse)
async def agent_quality(agent_id: str, db: DbSession, current_user: CurrentUser) -> AgentQualityResponse:
    """How each version of the agent did: answered, failed, failing checks, people's verdicts."""
    try:
        return await _service().quality(
            identity=TenantPrincipal.from_user(current_user), user_id=current_user.id, agent_id=agent_id, db=db
        )
    except TraceNotFoundError as exc:
        raise _not_found() from exc


@router.get("/issues", response_model=IssuesResponse)
async def issues(
    db: DbSession, current_user: CurrentUser, agent_id: str | None = None, since_ms: int | None = None
) -> IssuesResponse:
    """The ways the user's runs go wrong, grouped and counted, with the latest examples."""
    try:
        return await _service().issues(
            identity=TenantPrincipal.from_user(current_user),
            user_id=current_user.id,
            agent_id=agent_id,
            since_ms=since_ms,
            db=db,
        )
    except TraceNotFoundError as exc:
        raise _not_found() from exc


@router.get("/agents/{agent_id}/judges", response_model=JudgesResponse)
async def list_judges(agent_id: str, db: DbSession, current_user: CurrentUser) -> JudgesResponse:
    """The judges applied to the agent's finished runs."""
    try:
        return await _service().judges(
            identity=TenantPrincipal.from_user(current_user), user_id=current_user.id, agent_id=agent_id, db=db
        )
    except TraceNotFoundError as exc:
        raise _not_found() from exc


@router.put("/agents/{agent_id}/judges", response_model=Judge)
async def save_judge(agent_id: str, body: JudgeRequest, db: DbSession, current_user: CurrentUser) -> Judge:
    """Create or change a judge (a new rubric or model is a new version)."""
    try:
        return await _service().save_judge(
            identity=TenantPrincipal.from_user(current_user),
            user_id=current_user.id,
            agent_id=agent_id,
            body=body,
            db=db,
        )
    except TraceNotFoundError as exc:
        raise _not_found() from exc


@router.delete("/agents/{agent_id}/judges/{judge_id}", status_code=204, response_class=Response)
async def delete_judge(agent_id: str, judge_id: str, db: DbSession, current_user: CurrentUser) -> Response:
    """Remove a judge; the verdicts it gave stay."""
    try:
        await _service().delete_judge(
            identity=TenantPrincipal.from_user(current_user),
            user_id=current_user.id,
            agent_id=agent_id,
            judge_id=judge_id,
            db=db,
        )
    except TraceNotFoundError as exc:
        raise _not_found() from exc
    return Response(status_code=204)
