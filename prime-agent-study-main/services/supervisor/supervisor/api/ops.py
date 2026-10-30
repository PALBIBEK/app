"""The worker fleet for operators (``supervisor.temporal.fleet``).

``GET /v1/ops/workers`` says whether anyone serves the session queue, how many runs the fleet
can carry and carries now, what is waiting, and plain alerts (``no_workers``, ``backlog``,
``at_capacity``). ``GET /v1/ops/workers/metrics`` is the same as Prometheus text, for alerting
(``deploy/alerts/workers.rules.yml``). ``GET /v1/ops/metrics`` is this API's responses by
status code and whether each dependency answers (``deploy/alerts/platform.rules.yml``).
None names a tenant, a session or a secret.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import PlainTextResponse

from supervisor.api import deps
from supervisor.obs import dependencies
from supervisor.obs.responses import ResponseCounts, counter_lines
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


@router.get("/rollout")
async def rollout_status(request: Request, _tenant: str = Depends(deps.current_tenant)) -> dict[str, Any]:
    """The worker deployment's versions (current, ramping, draining, drained); ``versioned``
    is false where workers run unversioned (``WORKER_DEPLOYMENT_NAME`` unset)."""
    from supervisor.config import TemporalSettings
    from supervisor.temporal import rollout

    state = getattr(request.app.state, "supervisor", None)
    workflows = getattr(state, "workflows", None)
    deployment = TemporalSettings.from_env().deployment_name
    if not deployment:
        return {"versioned": False}
    if workflows is None:
        raise HTTPException(status_code=503, detail="sessions do not run on Temporal here")
    try:
        found = await rollout.status(workflows.client, deployment)
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Temporal is unreachable: {exc}") from exc
    if found is None:
        return {"versioned": True, "deployment": deployment, "current_build": None, "versions": []}
    return {"versioned": True, **asdict(found)}


@router.get("/workers/metrics", response_class=PlainTextResponse)
async def workers_metrics(request: Request) -> PlainTextResponse:
    """The fleet as Prometheus gauges (counts only, like ``/metrics``)."""
    return PlainTextResponse(prometheus(await fleet_status(request)), media_type="text/plain; version=0.0.4")


@router.get("/metrics", response_class=PlainTextResponse)
async def platform_metrics(request: Request) -> PlainTextResponse:
    """This API's responses by status code since it started, and whether each dependency
    answers now, as Prometheus text (counts only)."""
    responses: ResponseCounts | None = getattr(request.app.state, "responses", None)
    lines = list(
        counter_lines(
            "yal_api_responses_total",
            "Responses this supervisor API sent, by status code.",
            responses.snapshot() if responses is not None else {},
        )
    )
    state = getattr(request.app.state, "supervisor", None)
    lines += dependencies.prometheus(await dependencies.dependencies_up(state) if state is not None else {})
    return PlainTextResponse("\n".join(lines) + "\n", media_type="text/plain; version=0.0.4")
