"""BFF AI health route E2E (ASGI) — T4/T5."""

import httpx
import pytest

from prime_agent.config import Settings, get_settings
from prime_agent.main import create_app
from prime_agent.services.auth_service import AuthService
from prime_agent.services.ai_client import AiClient
from prime_agent.web.dependencies import get_current_user, get_db


@pytest.fixture
def ai_settings(monkeypatch):
    settings = Settings(
        environment="development",
        secret_key="user-secret-key-also-at-least-32-bytes!!",
        ai_supervisor_base_url="http://supervisor.test",
        ai_service_jwt_secret="ai-service-jwt-secret-at-least-32-bytes!!",
    )
    get_settings.cache_clear()
    monkeypatch.setattr("prime_agent.config.get_settings", lambda: settings)
    monkeypatch.setattr("prime_agent.config.settings.get_settings", lambda: settings)
    monkeypatch.setattr("prime_agent.web.routes.ai.get_settings", lambda: settings)
    monkeypatch.setattr("prime_agent.services.ai_client.get_settings", lambda: settings)
    monkeypatch.setattr("prime_agent.security.service_jwt.get_settings", lambda: settings)
    monkeypatch.setattr("prime_agent.security.jwt_manager.get_settings", lambda: settings)
    yield settings
    get_settings.cache_clear()


@pytest.fixture
async def owner(db_session, make_user):
    return await make_user(email="ai-owner@example.com", name="AI Owner")


@pytest.fixture
async def client(db_session, owner, ai_settings, monkeypatch):
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["authorization"] = request.headers.get("authorization", "")
        return httpx.Response(200, json={"status": "ok", "service": "supervisor"})

    def make_client(settings=None):
        return AiClient(
            settings or ai_settings,
            transport=httpx.MockTransport(handler),
        )

    monkeypatch.setattr("prime_agent.web.routes.ai.AiClient.from_settings", make_client)

    app = create_app()

    async def override_db():
        yield db_session

    async def override_user():
        return owner

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = override_user
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as api:
        yield api, captured, owner


@pytest.mark.asyncio
async def test_ai_health_without_user_login_returns_401_before_upstream_call(
    db_session, ai_settings, monkeypatch
):
    called = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        called["n"] += 1
        return httpx.Response(200, json={"status": "ok"})

    monkeypatch.setattr(
        "prime_agent.web.routes.ai.AiClient.from_settings",
        lambda settings=None: AiClient(ai_settings, transport=httpx.MockTransport(handler)),
    )

    app = create_app()

    async def override_db():
        yield db_session

    app.dependency_overrides[get_db] = override_db
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as api:
        response = await api.get("/api/v1/ai/health")
    assert response.status_code == 401
    assert called["n"] == 0


@pytest.mark.asyncio
async def test_ai_health_with_user_sends_service_bearer_only_not_user_access_token(client):
    api, captured, owner = client
    response = await api.get("/api/v1/ai/health")
    assert response.status_code == 200
    body = response.json()
    assert body["tenant_id"] == owner.id
    assert body["principal_id"] == owner.id
    assert body["upstream"] == "supervisor"
    assert "token" not in body
    assert "access_token" not in body
    assert captured["authorization"].startswith("Bearer ")
    user_token = AuthService.create_access_token(owner.id)
    assert user_token not in captured["authorization"]


@pytest.mark.asyncio
async def test_ai_health_response_never_includes_service_token(client):
    api, captured, _owner = client
    response = await api.get("/api/v1/ai/health")
    assert response.status_code == 200
    raw = response.text
    service_bearer = captured["authorization"].removeprefix("Bearer ")
    assert service_bearer not in raw
