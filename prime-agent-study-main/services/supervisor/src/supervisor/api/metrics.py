"""Tenant-scoped metrics snapshot."""

from __future__ import annotations

from fastapi import APIRouter, Request

from supervisor.api import deps

router = APIRouter(tags=["metrics"])


@router.get("/metrics")
async def metrics_snapshot(request: Request) -> dict:
    """In-process tenant/session counters for ops (Prometheus export later)."""
    state = getattr(request.app.state, "supervisor", None)
    if state is None or state.metrics is None:
        return {"tenants": {}, "live_sandboxes": 0}
    return await state.metrics.snapshot(deps.get_manager(request))
