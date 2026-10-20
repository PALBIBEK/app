"""Shared fixtures for independent D7 Durable Memory acceptance."""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from supervisor.memory_service.app import create_app

TOKEN = "d7-memory-acceptance-token"


@pytest.fixture
def authed(monkeypatch: pytest.MonkeyPatch):
    """CI residual auth: static token gated by MEMORY_ALLOW_STATIC_TOKEN=1."""
    monkeypatch.setenv("MEMORY_SERVICE_TOKEN", TOKEN)
    monkeypatch.setenv("MEMORY_ALLOW_STATIC_TOKEN", "1")
    monkeypatch.setenv("ENV", "staging")
    monkeypatch.setenv("AUTH_REQUIRED", "true")
    monkeypatch.delenv("SERVICE_JWT_SECRET", raising=False)
    monkeypatch.delenv("MEMORY_SERVICE_JWT_SECRET", raising=False)
    app = create_app(database_url="sqlite+aiosqlite:///:memory:")
    with TestClient(app) as client:
        yield client


def headers(tenant: str = "d7-tenant-a") -> dict[str, str]:
    return {
        "Authorization": f"Bearer {TOKEN}",
        "X-Tenant-Id": tenant,
    }
