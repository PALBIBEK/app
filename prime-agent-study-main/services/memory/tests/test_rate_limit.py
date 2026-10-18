"""Memory mutating-route rate limit (D7 production MVP)."""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from memory_service.app import create_app
from memory_service.rate_limit import TokenBucketLimiter, rate_limit_key


@pytest.fixture
def limited(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("MEMORY_SERVICE_TOKEN", "rl-secret")
    monkeypatch.setenv("MEMORY_ALLOW_STATIC_TOKEN", "1")
    monkeypatch.setenv("MEMORY_RATE_LIMIT_PER_SECOND", "100")
    monkeypatch.setenv("MEMORY_RATE_LIMIT_BURST", "3")
    monkeypatch.delenv("SERVICE_JWT_SECRET", raising=False)
    monkeypatch.delenv("MEMORY_SERVICE_JWT_SECRET", raising=False)
    monkeypatch.delenv("AUTH_REQUIRED", raising=False)
    monkeypatch.delenv("ENV", raising=False)
    app = create_app(database_url="sqlite+aiosqlite:///:memory:")
    with TestClient(app) as client:
        yield client


def test_token_bucket_blocks_burst() -> None:
    lim = TokenBucketLimiter(rate_per_second=0.01, burst=2)
    assert lim.allow("tenant:a")
    assert lim.allow("tenant:a")
    assert not lim.allow("tenant:a")


def test_rate_limit_key_prefers_tenant() -> None:
    assert rate_limit_key(tenant_id="t1", client_ip="1.2.3.4") == "tenant:t1"
    assert rate_limit_key(tenant_id="", client_ip="1.2.3.4") == "ip:1.2.3.4"


def test_mutating_routes_return_429_when_bucket_empty(limited: TestClient) -> None:
    headers = {
        "Authorization": "Bearer rl-secret",
        "X-Tenant-Id": "rl-tenant",
    }
    body = {
        "scope": {"tenant": "rl-tenant", "subject": "s", "namespace": "n"},
        "content": "fact",
    }
    codes = [
        limited.post("/v1/memory/items", headers=headers, json=body).status_code
        for _ in range(5)
    ]
    assert 201 in codes
    assert 429 in codes
    assert limited.post("/v1/memory/items", headers=headers, json=body).status_code == 429


def test_get_routes_not_rate_limited_by_mutating_bucket(
    limited: TestClient,
) -> None:
    """Reads (GET) skip the mutating-route limiter."""
    headers = {
        "Authorization": "Bearer rl-secret",
        "X-Tenant-Id": "rl-tenant",
    }
    for _ in range(10):
        r = limited.get("/v1/metrics", headers=headers)
        assert r.status_code == 200
