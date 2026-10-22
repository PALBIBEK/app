"""The worker fleet for operators (``supervisor.temporal.fleet``).

``GET /v1/ops/workers`` says whether anyone serves the session queue, how many runs the fleet
can carry and carries now, what is waiting, and plain alerts (``no_workers``, ``backlog``,
``at_capacity``). ``GET /v1/ops/workers/metrics`` is the same as Prometheus text, for alerting
(``deploy/alerts/workers.rules.yml``). Neither names a tenant, a session or a secret.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import PlainTextResponse

from supervisor.api import deps
from supervisor.temporal.fleet import FleetStatus, assess, prometheus
from supervisor.temporal.sessions import TemporalUnavailable

router = APIRouter(prefix="/v1/ops", tags=["ops"])


async def fleet_status(request: Request) -> FleetStatus:
    state = getattr(request.app.state, "supervisor", None)
    workflows = getattr(state, "workflows", None)
    if state is None or workflows is None:
        raise HTTPException(status_code=503, detail="sessions do not run on Temporal here")
    try:
        queue = await workflows.queue_stats()
    except TemporalUnavailable as exc:
        raise HTTPException(status_code=503, detail=f"Temporal is unreachable: {exc}") from exc
    tracked = state.heartbeats is not None
    beats = await state.heartbeats.live(workflows.task_queue) if tracked else []
    return assess(
        workflows.task_queue, queue, beats, backlog_alert_s=workflows.backlog_alert_s, heartbeats=tracked
    )


@router.get("/workers")
async def workers(request: Request, _tenant: str = Depends(deps.current_tenant)) -> dict[str, Any]:
    """The fleet's health: ``ok``, ``degraded`` or ``down``, with what each worker carries."""
    return asdict(await fleet_status(request))


@router.get("/workers/metrics", response_class=PlainTextResponse)
async def workers_metrics(request: Request) -> PlainTextResponse:
    """The fleet as Prometheus gauges (counts only, like ``/metrics``)."""
    return PlainTextResponse(prometheus(await fleet_status(request)), media_type="text/plain; version=0.0.4")
