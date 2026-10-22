"""A session's runs: fetch one by id, list them, cancel one (supervisor.session.runs).

``GET .../runs/{run_id}`` answers ``202`` while the run is queued or going and ``200`` once it
has ended (as xAI's deferred completions do), always with the run: its status, final answer,
usage and error. ``POST .../cancel`` is idempotent (as OpenAI's is): a run that already ended
is returned as it is; a queued one is dropped before it runs; a running one is aborted.
"""

from __future__ import annotations

import asyncio
from datetime import UTC
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from supervisor.api import deps
from supervisor.api.workflow_errors import to_http
from supervisor.session.runs import RunView, run_view
from supervisor.temporal.sessions import SessionCommandError, TemporalUnavailable

router = APIRouter(prefix="/v1/sessions", tags=["runs"])

CANCEL_WAIT_S = 5.0
"""How long a cancel waits for the running run to stop before answering (it may still be
stopping: the answer then says ``in_progress`` and the client fetches it again)."""


class RunOut(BaseModel):
    run_id: str
    command_id: str
    status: str
    created_at_ms: int
    started_at_ms: int | None = None
    ended_at_ms: int | None = None
    stop_reason: str | None = None
    output_text: str | None = None
    error: dict[str, str] | None = None
    usage: dict[str, Any] = {}
    steps: int = 0
    tool_calls: int = 0
    pending_approval_id: str | None = None


def _out(v: RunView) -> RunOut:
    return RunOut(**{f: getattr(v, f) for f in RunOut.model_fields})


def _ms(dt: Any) -> int:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return int(dt.timestamp() * 1000)


async def _view(request: Request, session_id: str, run_id: str) -> RunView:
    row = await deps.get_commands(request).by_turn(session_id, run_id)
    if row is None:
        raise HTTPException(status_code=404, detail="run not found")
    events = await deps.get_reader(request).read_turn(session_id, run_id)
    return run_view(
        run_id=run_id, command_id=row.command_id, created_at_ms=_ms(row.created_at),
        command_status=(row.response or {}).get("status"), events=events,
    )


def _answer(v: RunView) -> JSONResponse:
    return JSONResponse(status_code=200 if v.done else 202, content=_out(v).model_dump())


@router.get("/{session_id}/runs", response_model=list[RunOut])
async def list_runs(
    session_id: str,
    request: Request,
    tenant_id: str = Depends(deps.current_tenant),
    limit: int = Query(default=20, ge=1, le=100),
) -> list[RunOut]:
    """The session's runs, newest first."""
    await deps.require_session_access(tenant_id, session_id, request)
    rows = await deps.get_commands(request).recent(session_id, limit=limit)
    events = await deps.get_reader(request).read(session_id)
    by_turn: dict[str, list[Any]] = {}
    for env in events:
        if env.turn_id:
            by_turn.setdefault(env.turn_id, []).append(env)
    return [
        _out(run_view(
            run_id=row.turn_id, command_id=row.command_id, created_at_ms=_ms(row.created_at),
            command_status=(row.response or {}).get("status"), events=by_turn.get(row.turn_id, []),
        ))
        for row in rows
    ]


@router.get("/{session_id}/runs/{run_id}", response_model=RunOut, responses={202: {"model": RunOut}})
async def get_run(
    session_id: str, run_id: str, request: Request, tenant_id: str = Depends(deps.current_tenant)
) -> JSONResponse:
    """One run by id: 202 while it is queued or going, 200 once it has ended."""
    await deps.require_session_access(tenant_id, session_id, request)
    return _answer(await _view(request, session_id, run_id))


@router.post("/{session_id}/runs/{run_id}/cancel", response_model=RunOut, responses={202: {"model": RunOut}})
async def cancel_run(
    session_id: str, run_id: str, request: Request, tenant_id: str = Depends(deps.current_tenant)
) -> JSONResponse:
    """Stop a run. Idempotent: an ended run is returned unchanged."""
    await deps.require_session_access(tenant_id, session_id, request)
    view = await _view(request, session_id, run_id)
    if view.done:
        return _answer(view)
    workflows = deps.get_workflows(request)
    try:
        if view.status == "queued":
            if workflows is not None and await workflows.cancel_queued(session_id, view.command_id):
                await deps.get_commands(request).mark(session_id, view.command_id, "cancelled")
            return _answer(await _view(request, session_id, run_id))
        # Running (or waiting for an approval): abort the session's current run.
        if workflows is not None:
            status = await workflows.status(session_id)
            if status.running_command == view.command_id:
                await workflows.abort(session_id)
        else:
            await deps.get_manager(request).abort(session_id)
    except (SessionCommandError, TemporalUnavailable) as exc:
        raise to_http(exc) from exc
    loop = asyncio.get_running_loop()
    deadline = loop.time() + CANCEL_WAIT_S
    while not view.done and loop.time() < deadline:
        await asyncio.sleep(0.2)
        view = await _view(request, session_id, run_id)
    return _answer(view)
