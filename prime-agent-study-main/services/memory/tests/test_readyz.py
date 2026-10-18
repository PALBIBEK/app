"""D2 memory readiness — /v1/readyz vs process-only /v1/healthz."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from starlette.testclient import TestClient

from memory_service.app import create_app
from memory_service.service import MemoryService


@pytest.fixture
def client() -> TestClient:
    app = create_app(database_url="sqlite+aiosqlite:///:memory:")
    with TestClient(app) as c:
        yield c


def test_readyz_ok(client: TestClient) -> None:
    r = client.get("/v1/readyz")
    assert r.status_code == 200
    assert r.json() == {"status": "ready"}


def test_readyz_503_database(client: TestClient) -> None:
    svc: MemoryService = client.app.state.memory
    svc.ping_db = AsyncMock(return_value=False)  # type: ignore[method-assign]
    r = client.get("/v1/readyz")
    assert r.status_code == 503
    body = r.json()
    assert body["status"] == "not_ready"
    assert body["reason"] == "database"


def test_healthz_public_when_readyz_not_ready(client: TestClient) -> None:
    svc: MemoryService = client.app.state.memory
    svc.ping_db = AsyncMock(return_value=False)  # type: ignore[method-assign]
    assert client.get("/v1/readyz").status_code == 503
    hz = client.get("/v1/healthz")
    assert hz.status_code == 200
    assert hz.json()["status"] == "ok"


def test_readyz_public_with_auth_required(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SERVICE_JWT_SECRET", "mem-jwt-secret")
    monkeypatch.setenv("AUTH_REQUIRED", "true")
    app = create_app(database_url="sqlite+aiosqlite:///:memory:")
    with TestClient(app) as c:
        # No Bearer — readyz must stay public (compose health probes)
        r = c.get("/v1/readyz")
        assert r.status_code == 200
        assert r.json() == {"status": "ready"}
        assert c.get("/v1/healthz").status_code == 200
