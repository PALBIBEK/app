"""Lightweight in-process metrics for multi-tenant ops."""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select

from supervisor.db.models import SessionIndexRow, SessionRow
from supervisor.obs.latency import recent_ttft
from supervisor.session import SessionManager

_ACTIVE = ("active", "paused", "awaiting_approval")


class MetricsRegistry:
    def __init__(self) -> None:
        self._request_counts: dict[str, int] = {}

    def record_request(self, tenant_id: str) -> None:
        self._request_counts[tenant_id] = self._request_counts.get(tenant_id, 0) + 1

    async def snapshot(self, manager: SessionManager, *, tenant_id: str | None = None) -> dict[str, Any]:
        """Per-tenant counters (only ``tenant_id``'s when given) and fleet-wide figures."""
        tenants: dict[str, dict[str, float | int]] = {}
        query = (
            select(
                SessionRow.tenant_id,
                func.count(SessionRow.id),
                func.coalesce(func.sum(SessionIndexRow.total_cost), 0),
            )
            .outerjoin(SessionIndexRow, SessionIndexRow.session_id == SessionRow.id)
            .where(SessionRow.status.in_(_ACTIVE))
            .group_by(SessionRow.tenant_id)
        )
        if tenant_id is not None:
            query = query.where(SessionRow.tenant_id == tenant_id)
        async with manager._db() as db:
            rows = (await db.execute(query)).all()
            ttft = await recent_ttft(db)
        for tenant, count, cost in rows:
            tenants[str(tenant)] = {
                "active_sessions": int(count),
                "total_cost": float(cost or 0.0),
                "requests": self._request_counts.get(str(tenant), 0),
            }
        live_sandboxes = len(manager._hands.live)
        return {
            "live_sandboxes": live_sandboxes,
            "tenants": tenants,
            "ttft": ttft.to_dict(),
        }


def setup_metrics(service_name: str = "yal-supervisor") -> MetricsRegistry:
    """Return a process-local metrics registry (OTel export later)."""
    del service_name
    return MetricsRegistry()
