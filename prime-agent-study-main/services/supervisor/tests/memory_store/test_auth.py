"""D1 memory HTTP auth — Bearer, tenant mismatch, IDOR on get-by-id."""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from supervisor.memory_service.app import create_app


@pytest.fixture
def authed(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("MEMORY_SERVICE_TOKEN", "mem-secret")
    monkeypatch.setenv("MEMORY_ALLOW_STATIC_TOKEN", "1")
    monkeypatch.delenv("AUTH_REQUIRED", raising=False)
    monkeypatch.delenv("ENV", raising=False)
    monkeypatch.delenv("SERVICE_JWT_SECRET", raising=False)
    monkeypatch.delenv("MEMORY_SERVICE_JWT_SECRET", raising=False)
    app = create_app(database_url="sqlite+aiosqlite:///:memory:")
    with TestClient(app) as client:
        yield client


def _headers(tenant: str = "tenant-a") -> dict[str, str]:
    return {
        "Authorization": "Bearer mem-secret",
        "X-Tenant-Id": tenant,
    }


def test_memory_no_bearer_401(authed: TestClient) -> None:
    r = authed.post(
        "/v1/memory/items",
        json={
            "scope": {"tenant": "tenant-a", "subject": "s", "namespace": "n"},
            "content": "secret fact",
        },
    )
    assert r.status_code == 401


def test_memory_healthz_public(authed: TestClient) -> None:
    assert authed.get("/v1/healthz").status_code == 200


def test_memory_scope_tenant_mismatch_403(authed: TestClient) -> None:
    r = authed.post(
        "/v1/memory/items",
        headers=_headers("tenant-a"),
        json={
            "scope": {"tenant": "tenant-b", "subject": "s", "namespace": "n"},
            "content": "cross tenant write",
        },
    )
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "TENANT_MISMATCH"


def test_memory_get_item_idor_404(authed: TestClient) -> None:
    created = authed.post(
        "/v1/memory/items",
        headers=_headers("tenant-a"),
        json={
            "scope": {"tenant": "tenant-a", "subject": "s", "namespace": "n"},
            "content": "owned by A",
        },
    )
    assert created.status_code == 201, created.text
    item_id = created.json()["id"]

    ok = authed.get(f"/v1/memory/items/{item_id}", headers=_headers("tenant-a"))
    assert ok.status_code == 200

    leak = authed.get(f"/v1/memory/items/{item_id}", headers=_headers("tenant-b"))
    assert leak.status_code == 404


def test_memory_auth_unconfigured_staging_503(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MEMORY_SERVICE_TOKEN", raising=False)
    monkeypatch.delenv("MEMORY_ALLOW_STATIC_TOKEN", raising=False)
    monkeypatch.delenv("SERVICE_JWT_SECRET", raising=False)
    monkeypatch.delenv("MEMORY_SERVICE_JWT_SECRET", raising=False)
    monkeypatch.setenv("ENV", "staging")
    app = create_app(database_url="sqlite+aiosqlite:///:memory:")
    with TestClient(app) as client:
        r = client.get("/v1/metrics")
        assert r.status_code == 503
        assert r.json()["detail"]["code"] == "AUTH_UNCONFIGURED"
        assert client.get("/v1/healthz").status_code == 200


def test_memory_static_token_rejected_without_allow_flag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Production: MEMORY_SERVICE_TOKEN alone is insufficient without allow flag."""
    monkeypatch.setenv("MEMORY_SERVICE_TOKEN", "mem-secret")
    monkeypatch.delenv("MEMORY_ALLOW_STATIC_TOKEN", raising=False)
    monkeypatch.delenv("SERVICE_JWT_SECRET", raising=False)
    monkeypatch.delenv("MEMORY_SERVICE_JWT_SECRET", raising=False)
    monkeypatch.setenv("ENV", "staging")
    monkeypatch.setenv("AUTH_REQUIRED", "true")
    app = create_app(database_url="sqlite+aiosqlite:///:memory:")
    with TestClient(app) as client:
        r = client.post(
            "/v1/memory/items",
            headers={
                "Authorization": "Bearer mem-secret",
                "X-Tenant-Id": "tenant-a",
            },
            json={
                "scope": {"tenant": "tenant-a", "subject": "s", "namespace": "n"},
                "content": "blocked",
            },
        )
        assert r.status_code == 503
        assert r.json()["detail"]["code"] == "AUTH_UNCONFIGURED"


def test_memory_jwt_tenant_from_claim(monkeypatch: pytest.MonkeyPatch) -> None:
    """D2/D7: tenant comes from JWT claim; X-Tenant-Id cannot override."""
    from datetime import UTC, datetime, timedelta
    from uuid import uuid4

    import jwt

    monkeypatch.delenv("MEMORY_SERVICE_TOKEN", raising=False)
    monkeypatch.delenv("MEMORY_ALLOW_STATIC_TOKEN", raising=False)
    monkeypatch.setenv("SERVICE_JWT_SECRET", "mem-jwt-secret")
    monkeypatch.setenv("MEMORY_JWT_AUDIENCE", "yal-memory")
    monkeypatch.setenv("MEMORY_JWT_ISSUER", "prime-agent-api")
    monkeypatch.delenv("AUTH_REQUIRED", raising=False)
    monkeypatch.delenv("ENV", raising=False)

    now = datetime.now(UTC)
    token = jwt.encode(
        {
            "sub": "prime-agent-api",
            "iss": "prime-agent-api",
            "aud": "yal-memory",
            "type": "service",
            "tenant_id": "tenant-a",
            "principal_id": "user-1",
            "iat": now,
            "exp": now + timedelta(hours=1),
            "jti": str(uuid4()),
        },
        "mem-jwt-secret",
        algorithm="HS256",
    )
    app = create_app(database_url="sqlite+aiosqlite:///:memory:")
    with TestClient(app) as client:
        ok = client.post(
            "/v1/memory/items",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "scope": {"tenant": "tenant-a", "subject": "s", "namespace": "n"},
                "content": "from claim",
            },
        )
        assert ok.status_code == 201, ok.text
        # Spoofed header must not change caller tenant (claim wins).
        spoof = client.post(
            "/v1/memory/items",
            headers={
                "Authorization": f"Bearer {token}",
                "X-Tenant-Id": "tenant-evil",
            },
            json={
                "scope": {"tenant": "tenant-a", "subject": "s", "namespace": "n"},
                "content": "header ignored",
            },
        )
        assert spoof.status_code == 201, spoof.text
        bad = client.post(
            "/v1/memory/items",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "scope": {"tenant": "tenant-b", "subject": "s", "namespace": "n"},
                "content": "cross",
            },
        )
        assert bad.status_code == 403
