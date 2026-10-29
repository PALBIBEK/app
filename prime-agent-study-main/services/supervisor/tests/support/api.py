"""The supervisor's HTTP API in process, for tests."""

from __future__ import annotations

from httpx import ASGITransport, AsyncClient

from supervisor.app import SupervisorState
from supervisor.main import create_app


def supervisor_api(state: SupervisorState, *, tenant: str = "t1", principal: str = "alice") -> AsyncClient:
    """A client of ``state``'s API, calling as ``principal`` of ``tenant``."""
    return AsyncClient(
        transport=ASGITransport(app=create_app(state=state)),
        base_url="http://supervisor",
        headers={"X-Tenant-Id": tenant, "X-Principal-Id": principal},
    )
