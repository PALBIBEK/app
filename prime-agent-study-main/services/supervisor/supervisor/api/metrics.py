"""Tenant-scoped metrics snapshot."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from supervisor.api import deps

router = APIRouter(tags=["metrics"])


@router.get("/metrics")
async def metrics_snapshot(request: Request, tenant_id: str = Depends(deps.current_tenant)) -> dict:
    """The caller's tenant counters (active sessions, cost, requests) and fleet-wide
    figures that name no tenant (live sandboxes, time to first token).

    It used to answer anyone, with every tenant's id, session count and spend.
    """
    state = getattr(request.app.state, "supervisor", None)
    if state is None or state.metrics is None:
        return {"tenants": {}, "live_sandboxes": 0}
    return await state.metrics.snapshot(deps.get_manager(request), tenant_id=tenant_id)
