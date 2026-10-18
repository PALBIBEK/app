"""Shared helpers for independent D6 tools-execute acceptance."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from supervisor.app import build_supervisor
from supervisor.catalog.bootstrap import bootstrap_composition_catalog, default_catalog_dir
from supervisor.catalog.store import CompositionCatalog
from supervisor.db.engine import open_db
from supervisor.main import create_app
from tests.support.session_env import TrackingProvider, make_catalog

TENANT_A = "d6-tenant-a"
TENANT_B = "d6-tenant-b"
HEADERS_A = {"X-Tenant-Id": TENANT_A}
HEADERS_B = {"X-Tenant-Id": TENANT_B}

OUTER_REPO = Path(__file__).resolve().parents[5]
AI_REPO = Path(__file__).resolve().parents[4]


@pytest.fixture
def shipped_catalog() -> CompositionCatalog:
    """Live shipped harness/tool_sets + compositions (coding-tools, githi-tools)."""
    return bootstrap_composition_catalog(default_catalog_dir(), fail_on_verify=False)


@pytest.fixture
def coding_tool_names() -> list[str]:
    path = default_catalog_dir() / "tool_sets" / "coding-tools.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    return sorted(data["tools"].keys())


@pytest.fixture
def githi_tool_names() -> list[str]:
    path = default_catalog_dir() / "tool_sets" / "githi-tools.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    return sorted(data["tools"].keys())


@pytest.fixture
async def supervisor_stack(tmp_path, shipped_catalog) -> AsyncIterator[dict[str, Any]]:
    """ASGI client + SupervisorState with shipped composition catalog."""
    tracker = TrackingProvider(base_dir=tmp_path / "sandboxes")
    async with open_db(f"sqlite+aiosqlite:///{tmp_path / 'd6.db'}") as (_, factory):
        state = build_supervisor(
            session_factory=factory,
            catalog=make_catalog(),
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
                "catalog": shipped_catalog,
                "app": app,
            }


def tool_set_from_catalog(catalog: CompositionCatalog, name: str) -> Any:
    arts = catalog.list_artifacts("tool_set", status="offered")
    matches = [a for a in arts if a.name == name]
    assert matches, f"expected offered tool_set {name!r} in catalog"
    return matches[0]
