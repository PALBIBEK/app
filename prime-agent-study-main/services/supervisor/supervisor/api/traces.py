"""Run traces: runs across the caller's sessions, one session's runs, one run's span tree,
and one span's full content (``supervisor.traces``).

Visibility is the sessions' (``supervisor.session.access``): a run is visible exactly when
its session is. A run's tree carries previews; a span's messages, arguments and results
come from ``.../spans/{span_id}``, so opening a trace never ships every tool output.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from supervisor.api import deps
from supervisor.db.models import SessionRow
from supervisor.traces.model import RunStatus
from supervisor.traces.store import TraceFilter, list_runs, load_run, session_runs

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
