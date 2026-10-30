"""``GET /metrics`` answers a verified caller, with its own tenant's counters only.

It used to answer anyone who could reach the API, with every tenant's id, active
session count and total spend.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from supervisor.app import build_supervisor
from supervisor.db.engine import open_db
from supervisor.harness.repository import hydrate_harness_catalog
from supervisor.main import create_app
from supervisor.obs.metrics import MetricsRegistry
from tests.support.auth import SECRET, ServiceAuth
from tests.support.platform import all_variants, shipped_harness_catalog
from tests.support.session_env import TrackingProvider

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def api(tmp_path: Path) -> AsyncIterator[AsyncClient]:
    async with open_db(f"sqlite+aiosqlite:///{tmp_path / 'm.db'}") as (_, factory):
        harnesses = all_variants(shipped_harness_catalog())
        await hydrate_harness_catalog(harnesses, factory)
        state = build_supervisor(
            session_factory=factory,
            catalog=harnesses,
            blob_dir=tmp_path / "blobs",
            sandbox_provider=TrackingProvider(),
            auth_required=True,
            service_jwt_secret=SECRET,
            metrics=MetricsRegistry(),
        )
        async with AsyncClient(transport=ASGITransport(app=create_app(state=state)), base_url="http://t") as client:
            yield client


async def test_metrics_need_a_verified_caller(api: AsyncClient) -> None:
    assert (await api.get("/metrics")).status_code == 401


async def test_a_tenant_sees_its_own_counters_and_no_one_elses(api: AsyncClient) -> None:
    for tenant in ("acme", "acme", "globex"):
        created = await api.post("/v1/sessions", json={"harness_version": "kg-v1"}, headers=ServiceAuth(tenant))
        assert created.status_code == 201, created.text

    acme = (await api.get("/metrics", headers=ServiceAuth("acme"))).json()
    assert set(acme["tenants"]) == {"acme"}
    assert acme["tenants"]["acme"]["active_sessions"] == 2
    assert "ttft" in acme  # fleet-wide figures name no tenant
    globex = (await api.get("/metrics", headers=ServiceAuth("globex"))).json()
    assert set(globex["tenants"]) == {"globex"}
