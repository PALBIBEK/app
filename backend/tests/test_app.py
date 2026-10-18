import httpx
import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from prime_agent.config.settings import Settings
from prime_agent.main import create_app
from prime_agent.schemas.harness import HarnessCreate
from prime_agent.schemas.tool import ToolCreate
from prime_agent.schemas.user import UserCreate, UserUpdate
from prime_agent.web.routes.auth import require_trusted_origin
from prime_agent.web.routes.health import get_db as health_get_db


@pytest.fixture
def app():
    return create_app()


@pytest.mark.asyncio
async def test_health_and_docs_are_available(app):
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        health = await client.get("/health")
        docs = await client.get("/docs")

    assert health.status_code == 200
    assert health.json()["status"] == "ok"
    assert docs.status_code == 200


@pytest.mark.asyncio
async def test_readiness_failure_is_503_without_database_error_details(app):
    class UnavailableDatabase:
        async def execute(self, statement):
            raise RuntimeError("internal-hostname:5432 secret detail")

    async def unavailable_db():
        yield UnavailableDatabase()

    app.dependency_overrides[health_get_db] = unavailable_db
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/ready")

    assert response.status_code == 503
    assert response.json()["database"] == "disconnected"
    assert "internal-hostname" not in response.text


def test_auth_routes_are_mounted_under_api_v1(app):
    paths = app.openapi()["paths"]

    assert "/api/v1/auth/register" in paths
    assert "/api/v1/auth/login" in paths
    assert "/api/v1/auth/refresh" in paths
    assert "/api/v1/auth/logout" in paths
    assert "/api/v1/users/me" in paths
    assert "/api/v1/admin/ping" in paths
    assert "/api/v1/auth/me" not in paths


def test_refresh_token_uses_http_only_cookie_not_request_body(app):
    operation = app.openapi()["paths"]["/api/v1/auth/refresh"]["post"]

    assert "requestBody" not in operation


@pytest.mark.asyncio
async def test_untrusted_browser_origin_is_rejected():
    request = httpx.Request(
        "POST",
        "http://test/api/v1/auth/login",
        headers={"Origin": "https://attacker.example"},
    )

    with pytest.raises(HTTPException) as exc_info:
        await require_trusted_origin(request)

    assert exc_info.value.status_code == 403


@pytest.mark.parametrize("schema,payload", [
    (UserCreate, {"email": "user@example.com", "name": "User Name", "password": "SecurePass123!", "role": "owner"}),
    (UserUpdate, {"role": "owner", "is_active": True}),
    (HarnessCreate, {"name": "h", "model": "gpt-4o", "user_id": "other-user"}),
    (ToolCreate, {"name": "t", "description": "d", "category": "search", "user_id": "other-user"}),
])
def test_public_schemas_reject_privileged_fields(schema, payload):
    with pytest.raises(ValidationError):
        schema.model_validate(payload)


def test_production_rejects_default_secret_and_insecure_refresh_cookie():
    with pytest.raises(ValidationError):
        Settings(environment="production")
