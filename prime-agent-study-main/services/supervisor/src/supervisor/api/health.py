"""Liveness and readiness — harness-agnostic."""

from __future__ import annotations

from fastapi import APIRouter, Request, Response

router = APIRouter(tags=["health"])


@router.get("/healthz")
async def healthz() -> dict[str, str]:
    """Liveness: process up, event loop responds. No external deps."""
    return {"status": "ok"}


@router.get("/readyz")
async def readyz(request: Request, response: Response) -> dict[str, str]:
    """Readiness: DB reachable, sandbox provider if needed, not draining.

    Must not call runtime.get_state() — one bad session is not a dead pod.
    """
    state = getattr(request.app.state, "supervisor", None)
    if state is None:
        response.status_code = 503
        return {"status": "not_ready", "reason": "not_configured"}

    if getattr(state, "draining", False):
        response.status_code = 503
        return {"status": "not_ready", "reason": "draining"}

    check_db = getattr(state, "check_db", None)
    if check_db is not None:
        try:
            ok = await check_db()
        except Exception:
            ok = False
        if not ok:
            response.status_code = 503
            return {"status": "not_ready", "reason": "database"}

    check_sandbox = getattr(state, "check_sandbox", None)
    if check_sandbox is not None:
        try:
            ok = await check_sandbox()
        except Exception:
            ok = False
        if not ok:
            response.status_code = 503
            return {"status": "not_ready", "reason": "sandbox"}

    workflows = getattr(state, "workflows", None)
    if workflows is not None and not await workflows.check_reachable():
        # Every session runs as a Temporal workflow: no Temporal, no sessions.
        response.status_code = 503
        return {"status": "not_ready", "reason": "temporal"}

    catalog = getattr(state, "composition_catalog", None)
    if catalog is not None:
        from supervisor.catalog.loading.verify import list_catalog_problems

        catalog_problems = list_catalog_problems(catalog)
        if catalog_problems:
            response.status_code = 503
            return {
                "status": "not_ready",
                "reason": "catalog",
                "problems": catalog_problems[:8],
            }

    return {"status": "ready"}
