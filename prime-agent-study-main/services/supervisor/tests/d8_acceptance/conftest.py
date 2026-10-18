"""Shared helpers for independent D8 approvals + budgets acceptance."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from supervisor.app import build_supervisor
from supervisor.catalog.bootstrap import bootstrap_composition_catalog, default_catalog_dir
from supervisor.catalog.store import CompositionCatalog
from supervisor.db.engine import open_db
from supervisor.harness import ApprovalSpec, BudgetSpec
from supervisor.main import create_app
from tests.support.session_env import TrackingProvider, make_catalog, make_manifest

TENANT_A = "d8-tenant-a"
TENANT_B = "d8-tenant-b"
HEADERS_A = {"X-Tenant-Id": TENANT_A}
HEADERS_B = {"X-Tenant-Id": TENANT_B}


@pytest.fixture
def shipped_catalog() -> CompositionCatalog:
    return bootstrap_composition_catalog(default_catalog_dir(), fail_on_verify=False)


@pytest.fixture
async def supervisor_stack(tmp_path, shipped_catalog) -> AsyncIterator[dict[str, Any]]:
    tracker = TrackingProvider(base_dir=tmp_path / "sandboxes")
    catalog = make_catalog()
    # Patch kg-v1 with budget + approvals for D8 matrix.
    base = make_manifest("kg-v1", requires_sandbox=False, image_digest=None)
    base = replace(
        base,
        budget=BudgetSpec(max_cost_per_session=5.0),
        approvals=ApprovalSpec(
            timeout_s=300,
            on_timeout="deny",
            require_approval=("echo",),
        ),
    )
    catalog.register(base)
    async with open_db(f"sqlite+aiosqlite:///{tmp_path / 'd8.db'}") as (_, factory):
        state = build_supervisor(
            session_factory=factory,
            catalog=catalog,
            blob_dir=tmp_path / "blobs",
            live_queue_max=8,
            sandbox_provider=tracker,
            composition_catalog=shipped_catalog,
        )
        app = create_app(state=state)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            yield {
                "client": client,
                "state": state,
                "factory": factory,
                "tracker": tracker,
                "catalog": catalog,
                "app": app,
                "tmp_path": tmp_path,
            }
