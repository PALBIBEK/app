"""Run traces: runs across the caller's sessions, one session's runs, one run's span tree,
and one span's full content (``supervisor.traces``).

Visibility is the sessions' (``supervisor.session.access``): a run is visible exactly when
its session is. A run's tree carries previews; a span's messages, arguments and results
come from ``.../spans/{span_id}``, so opening a trace never ships every tool output.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field

from supervisor.api import deps
from supervisor.db.models import SessionRow
from supervisor.traces.issues import list_issues
from supervisor.traces.judges import delete_judge, list_judges, save_judge
from supervisor.traces.model import RunStatus
from supervisor.traces.scores import (
    Feedback,
    clear_feedback,
    run_scores,
    set_feedback,
    version_quality,
)
from supervisor.traces.store import (
    TraceFilter,
    list_runs,
    load_run,
    run_events,
    session_runs,
    stored_summary,
)

router = APIRouter(tags=["traces"])

_STATUSES = frozenset(s.value for s in RunStatus)


async def _session(request: Request, tenant_id: str, session_id: str) -> SessionRow:
    await deps.require_session_access(tenant_id, session_id, request)
    async with deps.get_session_factory(request)() as db:
        row = await db.get(SessionRow, session_id)
    if row is None:
        raise HTTPException(status_code=404, detail="session not found")
    return row


@router.get("/v1/traces")
async def list_traces(
    request: Request,
    tenant_id: str = Depends(deps.current_tenant),
    status: list[str] = Query(default_factory=list),
    session_id: str | None = None,
    composition_id: str | None = None,
    composition_digest: str | None = None,
    errors: bool = False,
    denied: bool = False,
    failing_checks: bool = False,
    feedback: str | None = Query(default=None, pattern="^(good|bad)$"),
    q: str | None = Query(default=None, max_length=200),
    since_ms: int | None = None,
    before_ms: int | None = None,
    limit: int = Query(default=50, ge=1, le=200),
) -> dict[str, Any]:
    """The caller's runs, newest first. ``before_ms`` takes the previous page's ``next_before_ms``."""
    if unknown := set(status) - _STATUSES:
        raise HTTPException(status_code=422, detail=f"unknown status: {', '.join(sorted(unknown))}")
    items, cursor = await list_runs(
        deps.get_session_factory(request),
        deps.get_reader(request),
        tenant_id=tenant_id,
        principal_id=deps.request_principal_id(request, tenant_id),
        filters=TraceFilter(
            statuses=tuple(status),
            session_id=session_id,
            composition_id=composition_id,
            composition_digest=composition_digest,
            with_errors=errors,
            with_denials=denied,
            failing_checks=failing_checks,
            feedback=feedback,
            text=q.strip() if q and q.strip() else None,
            since_ms=since_ms,
            before_ms=before_ms,
        ),
        limit=limit,
    )
    return {"items": items, "next_before_ms": cursor}


@router.get("/v1/sessions/{session_id}/traces")
async def list_session_traces(
    session_id: str, request: Request, tenant_id: str = Depends(deps.current_tenant)
) -> dict[str, Any]:
    """Every run of the session, oldest first: the session replayed turn by turn."""
    row = await _session(request, tenant_id, session_id)
    items = await session_runs(deps.get_session_factory(request), deps.get_reader(request), row)
    return {"session_id": session_id, "status": row.status, "items": items}


@router.get("/v1/sessions/{session_id}/runs/{run_id}/trace")
async def get_run_trace(
    session_id: str, run_id: str, request: Request, tenant_id: str = Depends(deps.current_tenant)
) -> dict[str, Any]:
    """The run as a tree of spans (flat, each naming its parent) with its rollup; no span content."""
    row = await _session(request, tenant_id, session_id)
    trace = await load_run(deps.get_session_factory(request), deps.get_reader(request), row, run_id)
    if trace is None:
        raise HTTPException(status_code=404, detail="run not found")
    out = trace.to_dict()
    out["agent"] = trace.spans[0].name if trace.spans else None
    out["harness_version"] = row.harness_version
    pins = row.composition_pins if isinstance(row.composition_pins, dict) else {}
    out["composition_id"] = pins.get("composition_id")
    out["composition_digest"] = pins.get("composition_digest")
    out["scores"] = await run_scores(deps.get_session_factory(request), session_id, run_id)
    out["summary"] = await stored_summary(deps.get_session_factory(request), session_id, run_id)
    return out


@router.get("/v1/sessions/{session_id}/runs/{run_id}/trace/spans/{span_id}")
async def get_trace_span(
    session_id: str, run_id: str, span_id: str, request: Request, tenant_id: str = Depends(deps.current_tenant)
) -> dict[str, Any]:
    """One span with its full content: messages, arguments, results, the change it made."""
    row = await _session(request, tenant_id, session_id)
    trace = await load_run(deps.get_session_factory(request), deps.get_reader(request), row, run_id)
    span = trace.span(span_id) if trace is not None else None
    if span is None:
        raise HTTPException(status_code=404, detail="span not found")
    return span.to_dict(full=True)


@router.get("/v1/sessions/{session_id}/runs/{run_id}/trace/events")
async def get_run_events(
    session_id: str, run_id: str, request: Request, tenant_id: str = Depends(deps.current_tenant)
) -> dict[str, Any]:
    """The events the trace was built from, as recorded: the run's own and the session's
    (a sandbox starting) that happened while it ran."""
    await _session(request, tenant_id, session_id)
    events = await run_events(deps.get_reader(request), session_id, run_id)
    if not events:
        raise HTTPException(status_code=404, detail="run not found")
    return {"session_id": session_id, "run_id": run_id, "events": events}


class FeedbackIn(BaseModel):
    good: bool
    span_id: str | None = Field(default=None, max_length=32)
    """A span of the run; none for the whole run."""
    comment: str | None = Field(default=None, max_length=2000)


@router.put("/v1/sessions/{session_id}/runs/{run_id}/feedback")
async def put_feedback(
    session_id: str,
    run_id: str,
    body: FeedbackIn,
    request: Request,
    tenant_id: str = Depends(deps.current_tenant),
) -> dict[str, Any]:
    """The caller's verdict on the run, or on one of its spans; replaces any they gave."""
    row = await _session(request, tenant_id, session_id)
    trace = await load_run(deps.get_session_factory(request), deps.get_reader(request), row, run_id)
    if trace is None:
        raise HTTPException(status_code=404, detail="run not found")
    if body.span_id is not None and trace.span(body.span_id) is None:
        raise HTTPException(status_code=404, detail="span not found")
    return await set_feedback(
        deps.get_session_factory(request),
        row,
        run_id,
        principal_id=deps.request_principal_id(request, tenant_id),
        feedback=Feedback(good=body.good, span_id=body.span_id, comment=body.comment),
    )


@router.delete("/v1/sessions/{session_id}/runs/{run_id}/feedback", status_code=204)
async def delete_feedback(
    session_id: str,
    run_id: str,
    request: Request,
    span_id: str | None = None,
    tenant_id: str = Depends(deps.current_tenant),
) -> Response:
    """Take back the caller's verdict on the run (or the span)."""
    row = await _session(request, tenant_id, session_id)
    gone = await clear_feedback(
        deps.get_session_factory(request),
        row,
        run_id,
        principal_id=deps.request_principal_id(request, tenant_id),
        span_id=span_id,
    )
    if not gone:
        raise HTTPException(status_code=404, detail="no feedback of yours on that")
    return Response(status_code=204)


@router.get("/v1/traces/versions")
async def trace_versions(
    request: Request,
    composition_id: str = Query(min_length=1, max_length=255),
    tenant_id: str = Depends(deps.current_tenant),
) -> dict[str, Any]:
    """How each version of a composition did across the caller's finished runs."""
    versions = await version_quality(
        deps.get_session_factory(request),
        tenant_id=tenant_id,
        principal_id=deps.request_principal_id(request, tenant_id),
        composition_id=composition_id,
    )
    return {"composition_id": composition_id, "versions": versions}


@router.get("/v1/traces/issues")
async def trace_issues(
    request: Request,
    composition_id: str | None = Query(default=None, max_length=255),
    since_ms: int | None = None,
    limit: int = Query(default=30, ge=1, le=100),
    tenant_id: str = Depends(deps.current_tenant),
) -> dict[str, Any]:
    """The ways the caller's runs go wrong, grouped and counted, with the latest examples."""
    issues = await list_issues(
        deps.get_session_factory(request),
        tenant_id=tenant_id,
        principal_id=deps.request_principal_id(request, tenant_id),
        composition_id=composition_id,
        since_ms=since_ms,
        limit=limit,
    )
    return {"issues": issues}


class JudgeIn(BaseModel):
    composition_id: str = Field(min_length=1, max_length=255)
    name: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9][A-Za-z0-9 _.-]*$")
    rubric: str = Field(min_length=10, max_length=4000)
    sample_rate: float = Field(default=1.0, ge=0.0, le=1.0)
    """Share of finished runs judged (0.25: one in four, always the same ones)."""
    model: str | None = Field(default=None, max_length=255)
    enabled: bool = True


@router.get("/v1/traces/judges")
async def get_judges(
    request: Request,
    composition_id: str = Query(min_length=1, max_length=255),
    tenant_id: str = Depends(deps.current_tenant),
) -> dict[str, Any]:
    """The judges applied to a composition's finished runs."""
    judges = await list_judges(deps.get_session_factory(request), tenant_id=tenant_id, composition_id=composition_id)
    return {"composition_id": composition_id, "judges": judges}


@router.put("/v1/traces/judges")
async def put_judge(body: JudgeIn, request: Request, tenant_id: str = Depends(deps.current_tenant)) -> dict[str, Any]:
    """Create a judge, or change one (a new rubric or model is a new version)."""
    return await save_judge(
        deps.get_session_factory(request),
        tenant_id=tenant_id,
        composition_id=body.composition_id,
        name=body.name.strip(),
        rubric=body.rubric.strip(),
        sample_rate=body.sample_rate,
        model=(body.model or "").strip() or None,
        enabled=body.enabled,
        created_by=deps.request_principal_id(request, tenant_id),
    )


@router.delete("/v1/traces/judges/{judge_id}", status_code=204)
async def remove_judge(judge_id: str, request: Request, tenant_id: str = Depends(deps.current_tenant)) -> Response:
    """Remove a judge; the verdicts it gave stay."""
    if not await delete_judge(deps.get_session_factory(request), tenant_id=tenant_id, judge_id=judge_id):
        raise HTTPException(status_code=404, detail="judge not found")
    return Response(status_code=204)
