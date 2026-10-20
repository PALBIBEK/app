"""Shared fixtures for independent D7 FULL-PRODUCTION Memory acceptance."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import jwt
import pytest
from starlette.testclient import TestClient

from supervisor.memory_service.app import create_app

JWT_SECRET = "d7-prod-memory-jwt-secret-at-least-32b!!"
JWT_AUD = "yal-memory"
JWT_ISS = "prime-agent-api"
STATIC_TOKEN = "d7-prod-static-token-should-not-be-primary"


def mint_service_jwt(
    *,
    tenant_id: str = "tenant-jwt-a",
    principal_id: str = "user-1",
    secret: str = JWT_SECRET,
    aud: str = JWT_AUD,
    iss: str = JWT_ISS,
) -> str:
    now = datetime.now(UTC)
    return jwt.encode(
        {
            "sub": "prime-agent-api",
            "iss": iss,
            "aud": aud,
            "type": "service",
            "tenant_id": tenant_id,
            "principal_id": principal_id,
            "iat": now,
            "exp": now + timedelta(hours=1),
            "jti": str(uuid4()),
        },
        secret,
        algorithm="HS256",
    )


@pytest.fixture
def jwt_only(monkeypatch: pytest.MonkeyPatch):
    """Staging-shaped: JWT secret configured; static token path not the primary."""
    monkeypatch.delenv("MEMORY_SERVICE_TOKEN", raising=False)
    monkeypatch.delenv("MEMORY_ALLOW_STATIC_TOKEN", raising=False)
    monkeypatch.setenv("SERVICE_JWT_SECRET", JWT_SECRET)
    monkeypatch.setenv("MEMORY_JWT_AUDIENCE", JWT_AUD)
    monkeypatch.setenv("MEMORY_JWT_ISSUER", JWT_ISS)
    monkeypatch.setenv("ENV", "staging")
    monkeypatch.setenv("AUTH_REQUIRED", "true")
    app = create_app(database_url="sqlite+aiosqlite:///:memory:")
    with TestClient(app) as client:
        yield client


@pytest.fixture
def static_only_staging(monkeypatch: pytest.MonkeyPatch):
    """Staging with static shared token only — production must reject as primary path."""
    monkeypatch.setenv("MEMORY_SERVICE_TOKEN", STATIC_TOKEN)
    monkeypatch.delenv("SERVICE_JWT_SECRET", raising=False)
    monkeypatch.delenv("MEMORY_SERVICE_JWT_SECRET", raising=False)
    monkeypatch.delenv("MEMORY_ALLOW_STATIC_TOKEN", raising=False)
    monkeypatch.setenv("ENV", "staging")
    monkeypatch.setenv("AUTH_REQUIRED", "true")
    app = create_app(database_url="sqlite+aiosqlite:///:memory:")
    with TestClient(app) as client:
        yield client
