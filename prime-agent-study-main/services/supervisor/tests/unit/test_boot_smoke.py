"""Boot the real app through its production lifespan.

Guards against the service failing to start (broken imports after a refactor,
schema errors, bad bootstrap wiring) — failures that unit tests building their
own SupervisorState never see.
"""

from __future__ import annotations

import importlib
import pkgutil

import pytest
from fastapi.testclient import TestClient

import supervisor

# Unfinished packages that are allowed not to import.
_OPTIONAL_PREFIXES = (
    "supervisor.core",  # unused, unfinished refactor
    "supervisor.seams.observable",  # unused, unfinished refactor
)


def test_every_module_imports() -> None:
    failures = {}
    for mod in pkgutil.walk_packages(supervisor.__path__, "supervisor."):
        if mod.name.startswith(_OPTIONAL_PREFIXES):
            continue
        try:
            importlib.import_module(mod.name)
        except Exception as exc:  # noqa: BLE001 - report every failure at once
            failures[mod.name] = f"{type(exc).__name__}: {exc}"
    assert not failures, "modules failed to import:\n" + "\n".join(
        f"  {k}: {v}" for k, v in failures.items()
    )


def _boot(tmp_path, monkeypatch, temporal_address: str):  # type: ignore[no-untyped-def]
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path / 'boot.db'}")
    monkeypatch.setenv("SANDBOX_PROVIDER", "none")
    monkeypatch.setenv("TEMPORAL_ADDRESS", temporal_address)
    from supervisor.main import create_app

    return TestClient(create_app())


@pytest.fixture
def booted_app(tmp_path, monkeypatch, temporal_env):  # type: ignore[no-untyped-def]
    with _boot(tmp_path, monkeypatch, temporal_env.client.service_client.config.target_host) as c:
        yield c


def test_not_ready_without_temporal(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    with _boot(tmp_path, monkeypatch, "127.0.0.1:1") as client:
        assert client.get("/healthz").status_code == 200  # alive, just not ready
        ready = client.get("/readyz")
        assert (ready.status_code, ready.json()["reason"]) == (503, "temporal")


def test_app_boots_and_reports_ready(booted_app: TestClient) -> None:
    assert booted_app.get("/healthz").status_code == 200
    ready = booted_app.get("/readyz")
    assert ready.status_code == 200, ready.text


def test_shipped_catalog_and_harness_versions_load(booted_app: TestClient) -> None:
    resp = booted_app.get("/v1/catalog/compositions", headers={"X-Tenant-Id": "t1"})
    assert resp.status_code == 200, resp.text
    assert resp.json(), "shipped compositions should load from harness/compositions"

    harness_catalog = booted_app.app.state.supervisor.manager._catalog  # noqa: SLF001
    versions = booted_app.portal.call(harness_catalog.list_available)
    assert versions, "harness versions from harness/versions should be registered"
