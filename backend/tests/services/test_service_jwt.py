"""Service JWT claim isolation tests."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import jwt
import pytest

from prime_agent.config import Settings
from prime_agent.schemas.ai_identity import TenantPrincipal
from prime_agent.security.jwt_manager import JWTManager
from prime_agent.security.service_jwt import ServiceJwtMinter


SECRET = "ai-service-jwt-secret-at-least-32-bytes!!"
USER_SECRET = "user-secret-key-also-at-least-32-bytes!!"


def _settings() -> Settings:
    return Settings(
        environment="development",
        secret_key=USER_SECRET,
        ai_service_jwt_secret=SECRET,
        ai_service_jwt_issuer="prime-agent-api",
        ai_service_jwt_audience="yal-supervisor",
        ai_service_id="prime-agent-bff",
        ai_service_jwt_ttl_seconds=60,
    )


def test_service_jwt_claims_differ_from_user_jwt_iss_aud_type(monkeypatch):
    settings = _settings()
    monkeypatch.setattr(
        "prime_agent.security.jwt_manager.get_settings",
        lambda: settings,
    )
    monkeypatch.setattr(
        "prime_agent.security.service_jwt.get_settings",
        lambda: settings,
    )

    user_token = JWTManager.create_access_token("user-1")
    user_payload = jwt.decode(
        user_token,
        USER_SECRET,
        algorithms=["HS256"],
        audience="prime-agent-web",
        issuer="prime-agent-api",
    )
    assert user_payload["type"] == "access"
    assert user_payload["aud"] == "prime-agent-web"

    service_token = ServiceJwtMinter(settings).mint(
        TenantPrincipal(tenant_id="user-1", principal_id="user-1")
    )
    service_payload = jwt.decode(
        service_token,
        SECRET,
        algorithms=["HS256"],
        audience="yal-supervisor",
        issuer="prime-agent-api",
    )
    assert service_payload["type"] == "service"
    assert service_payload["aud"] == "yal-supervisor"
    assert service_payload["tenant_id"] == "user-1"
    assert service_payload["sub"] == "prime-agent-bff"
    assert service_payload["aud"] != user_payload["aud"]
    assert service_payload["type"] != user_payload["type"]


def test_user_access_jwt_rejected_by_service_audience():
    settings = _settings()
    now = datetime.now(UTC)
    user_shaped = jwt.encode(
        {
            "sub": "user-1",
            "exp": now + timedelta(minutes=30),
            "iat": now,
            "jti": str(uuid4()),
            "iss": "prime-agent-api",
            "aud": "prime-agent-web",
            "type": "access",
        },
        SECRET,
        algorithm="HS256",
    )
    with pytest.raises(jwt.exceptions.PyJWTError):
        jwt.decode(
            user_shaped,
            SECRET,
            algorithms=["HS256"],
            audience="yal-supervisor",
            issuer="prime-agent-api",
        )


def test_production_settings_require_ai_supervisor_base_url_and_service_credential():
    with pytest.raises(ValueError, match="AI_SUPERVISOR_BASE_URL"):
        Settings(
            environment="production",
            secret_key="prod-secret-key-at-least-32-bytes-long",
            refresh_cookie_secure=True,
            ai_service_jwt_secret="ai-service-jwt-secret-at-least-32-bytes!!",
        )
    with pytest.raises(ValueError, match="AI_SERVICE_JWT_SECRET"):
        Settings(
            environment="production",
            secret_key="prod-secret-key-at-least-32-bytes-long",
            refresh_cookie_secure=True,
            ai_supervisor_base_url="http://supervisor:8080",
        )
