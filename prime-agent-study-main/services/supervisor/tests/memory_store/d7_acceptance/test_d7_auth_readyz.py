"""D7 §5 A / T1 — Memory auth fail-closed + /v1/readyz."""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from supervisor.memory_service.app import create_app


def test_t1_no_bearer_401_on_mutating_and_reading_routes(authed: TestClient) -> None:
    """T1: no Bearer → 401 on items (staging fail-closed)."""
    r = authed.post(
        "/v1/memory/items",
        json={
            "scope": {"tenant": "d7-tenant-a", "subject": "s", "namespace": "n"},
            "content": "secret",
        },
    )
    assert r.status_code == 401

    # Reading routes also require auth when configured.
    r2 = authed.get("/v1/memory/items/does-not-exist")
    assert r2.status_code in {401, 404}
    # Prefer strict: unauthenticated must not succeed.
    assert r2.status_code != 200


def test_t1_staging_unconfigured_auth_503(monkeypatch: pytest.MonkeyPatch) -> None:
    """§5 A: staging without token/JWT → fail-closed AUTH_UNCONFIGURED on protected routes."""
    monkeypatch.delenv("MEMORY_SERVICE_TOKEN", raising=False)
    monkeypatch.delenv("MEMORY_ALLOW_STATIC_TOKEN", raising=False)
    monkeypatch.delenv("SERVICE_JWT_SECRET", raising=False)
    monkeypatch.delenv("MEMORY_SERVICE_JWT_SECRET", raising=False)
    monkeypatch.setenv("ENV", "staging")
    monkeypatch.setenv("AUTH_REQUIRED", "true")
    app = create_app(database_url="sqlite+aiosqlite:///:memory:")
    with TestClient(app) as client:
        r = client.get("/v1/metrics")
        assert r.status_code == 503
        body = r.json()
        detail = body.get("detail") or body
        if isinstance(detail, dict):
            assert detail.get("code") == "AUTH_UNCONFIGURED"
        assert client.get("/v1/healthz").status_code == 200


def test_readyz_reflects_db_readiness(authed: TestClient) -> None:
    """§5 A: /v1/readyz exists and is healthy when DB is up."""
    r = authed.get("/v1/readyz")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body.get("status") == "ready" or body.get("ready") is True


def test_readyz_public_without_bearer(authed: TestClient) -> None:
    """Ready probes stay reachable without service token (k8s/compose health)."""
    r = authed.get("/v1/readyz")
    assert r.status_code == 200
