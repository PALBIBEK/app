"""Platform status + ready-gate E2E tests (D2 / Sprint 7)."""

from __future__ import annotations

import httpx
import pytest
from fastapi import HTTPException

from prime_agent.config import Settings, get_settings
from prime_agent.db.session import get_db as session_get_db
from prime_agent.main import create_app
from prime_agent.schemas.ai_identity import TenantPrincipal
from prime_agent.services.ai_client import AiClient, AiPlatformNotReadyError, ReadyzResult
from prime_agent.services.auth_service import AuthService
from prime_agent.services.platform_status_service import PlatformStatusService
from prime_agent.web.dependencies import get_current_user, get_db
from prime_agent.web.routes import health as health_routes


SECRET = "ai-service-jwt-secret-at-least-32-bytes!!"


@pytest.fixture
def platform_settings(monkeypatch):
    settings = Settings(
        environment="development",
        secret_key="user-secret-key-also-at-least-32-bytes!!",
        ai_supervisor_base_url="http://supervisor.test",
        ai_service_jwt_secret=SECRET,
        ai_platform_status_cache_ttl_seconds=0,
    )
    get_settings.cache_clear()
    PlatformStatusService.clear_cache()
    for target in (
        "prime_agent.config.get_settings",
        "prime_agent.config.settings.get_settings",
        "prime_agent.web.routes.platform.get_settings",
        "prime_agent.services.platform_status_service.get_settings",
        "prime_agent.services.ai_client.get_settings",
        "prime_agent.security.service_jwt.get_settings",
        "prime_agent.security.jwt_manager.get_settings",
    ):
        monkeypatch.setattr(target, lambda: settings)
    yield settings
    get_settings.cache_clear()
    PlatformStatusService.clear_cache()


@pytest.fixture
async def owner(db_session, make_user):
    return await make_user(email="platform@example.com", name="Platform User")


def _patch_client(monkeypatch, settings: Settings, handler):
    def make_client(s=None):
        return AiClient(s or settings, transport=httpx.MockTransport(handler))

    monkeypatch.setattr(
        "prime_agent.services.platform_status_service.AiClient.from_settings",
        make_client,
    )
    monkeypatch.setattr(
        "prime_agent.web.routes.platform.PlatformStatusService.from_settings",
        lambda s=None: PlatformStatusService(settings, client=make_client(settings)),
    )


@pytest.mark.asyncio
async def test_ai_client_readyz_parses_503_not_ready_body(platform_settings):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            503,
            json={"status": "not_ready", "reason": "draining", "problems": ["secret"]},
        )

    client = AiClient(platform_settings, transport=httpx.MockTransport(handler))
    result = await client.readyz(TenantPrincipal(tenant_id="t", principal_id="t"))
    assert isinstance(result, ReadyzResult)
    assert result.ready is False
    assert result.reason == "draining"
    assert "problems" not in result.raw


@pytest.mark.asyncio
async def test_platform_status_unauthenticated_returns_401_before_upstream_call(
    db_session, platform_settings, monkeypatch
):
    called = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        called["n"] += 1
        return httpx.Response(200, json={"status": "ready"})

    _patch_client(monkeypatch, platform_settings, handler)
    app = create_app()

    async def override_db():
        yield db_session

    app.dependency_overrides[get_db] = override_db
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as api:
        response = await api.get("/api/v1/platform/status")
    assert response.status_code == 401
    assert called["n"] == 0


@pytest.mark.asyncio
async def test_platform_status_ai_down_returns_200_ready_false_for_logged_in_user(
    db_session, owner, platform_settings, monkeypatch
):
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["authorization"] = request.headers.get("authorization", "")
        return httpx.Response(503, json={"status": "not_ready", "reason": "database"})

    _patch_client(monkeypatch, platform_settings, handler)
    app = create_app()

    async def override_db():
        yield db_session

    async def override_user():
        return owner

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = override_user
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as api:
        response = await api.get("/api/v1/platform/status")

    assert response.status_code == 200
    body = response.json()
    assert body["ready"] is False
    assert body["run_allowed"] is False
    assert body["components"]["supervisor"]["reason"] == "database"
    assert "token" not in body
    user_token = AuthService.create_access_token(owner.id)
    assert user_token not in captured.get("authorization", "")
    assert captured["authorization"].startswith("Bearer ")


@pytest.mark.asyncio
async def test_platform_status_ai_ready_returns_200_run_allowed_true(
    db_session, owner, platform_settings, monkeypatch
):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"status": "ready"})

    _patch_client(monkeypatch, platform_settings, handler)
    app = create_app()

    async def override_db():
        yield db_session

    async def override_user():
        return owner

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = override_user
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as api:
        response = await api.get("/api/v1/platform/status")

    assert response.status_code == 200
    body = response.json()
    assert body["ready"] is True
    assert body["run_allowed"] is True


@pytest.mark.asyncio
async def test_platform_status_uses_service_bearer_not_user_access_token(
    db_session, owner, platform_settings, monkeypatch
):
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["authorization"] = request.headers.get("authorization", "")
        return httpx.Response(200, json={"status": "ready"})

    _patch_client(monkeypatch, platform_settings, handler)
    app = create_app()

    async def override_db():
        yield db_session

    async def override_user():
        return owner

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = override_user
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as api:
        await api.get("/api/v1/platform/status")

    import jwt

    token = captured["authorization"].removeprefix("Bearer ")
    payload = jwt.decode(
        token,
        SECRET,
        algorithms=["HS256"],
        audience="yal-supervisor",
        issuer="prime-agent-api",
    )
    assert payload["type"] == "service"


@pytest.mark.asyncio
async def test_ensure_ai_platform_ready_raises_503_ai_platform_not_ready(
    db_session, owner, platform_settings, monkeypatch
):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"status": "not_ready", "reason": "draining"})

    _patch_client(monkeypatch, platform_settings, handler)
    app = create_app()

    async def override_db():
        yield db_session

    async def override_user():
        return owner

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = override_user
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as api:
        response = await api.post("/api/v1/platform/ensure-ready")

    assert response.status_code == 503
    detail = response.json()["detail"]
    assert detail["error_code"] == "AI_PLATFORM_NOT_READY"
    assert detail["run_allowed"] is False


@pytest.mark.asyncio
async def test_system_ready_still_200_when_ai_supervisor_unreachable(
    db_session, platform_settings, monkeypatch
):
    """T9: system /ready must not depend on AI plane."""

    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down", request=request)

    _patch_client(monkeypatch, platform_settings, boom)
    app = create_app()

    async def override_db():
        yield db_session

    # health router imports get_db from db.session
    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[session_get_db] = override_db
    app.dependency_overrides[health_routes.get_db] = override_db
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as api:
        ready = await api.get("/ready")
        status = await api.get("/api/v1/platform/status")

    assert ready.status_code == 200
    assert ready.json()["database"] == "connected"
    # unauthenticated status still 401 — AI down does not change that
    assert status.status_code == 401


@pytest.mark.asyncio
async def test_platform_status_reports_the_worker_fleet_in_counts_without_blocking_runs(
    db_session, owner, platform_settings, monkeypatch
):
    fleet = {
        "status": "down", "task_queue": "supervisor-sessions", "polling": 0,
        "workers": [{"worker_id": "host-a-1", "host": "host-a"}], "slots_max": 0, "slots_busy": 0,
        "backlog": 2, "backlog_age_s": 41.0, "add_rate": 0.1, "dispatch_rate": 0.0,
        "alerts": [
            {"code": "no_workers", "message": "No agent worker is running: new sessions and messages wait until one starts"},
            {"code": "something_new", "message": "ignored"},
        ],
        "checked_at_ms": 1,
    }

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/ops/workers":
            return httpx.Response(200, json=fleet)
        return httpx.Response(200, json={"status": "ready"})

    _patch_client(monkeypatch, platform_settings, handler)
    app = create_app()

    async def override_db():
        yield db_session

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = lambda: owner
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as api:
        body = (await api.get("/api/v1/platform/status")).json()

    assert body["run_allowed"] is True  # work waits in Temporal; it is not refused
    assert body["workers"] == {
        "status": "down", "polling": 0, "slots_busy": 0, "slots_max": 0, "backlog": 2, "backlog_age_s": 41.0,
        "alerts": [{"code": "no_workers", "message": fleet["alerts"][0]["message"]}],
    }
    assert "host-a" not in str(body)  # no hosts or worker ids reach the product
