"""Harness bind + runnable pin tests (D3 / Sprint 8)."""

from __future__ import annotations

import httpx
import jwt
import pytest

from prime_agent.config import Settings, get_settings
from prime_agent.main import create_app
from prime_agent.schemas.ai_identity import TenantPrincipal
from prime_agent.schemas.harness import HarnessBindRequest
from prime_agent.services.ai_client import AiClient
from prime_agent.services.harness_bind_service import (
    HarnessBindService,
    NotBoundError,
    StaleBindError,
)
from prime_agent.services.harness_service import HarnessService
from prime_agent.services.platform_status_service import PlatformStatusService
from prime_agent.web.dependencies import get_current_user, get_db
from tests.support.catalog import DIGEST, GITHI, PIN, catalog_response

SECRET = "ai-service-jwt-secret-at-least-32-bytes!!"


@pytest.fixture
def bind_settings(monkeypatch):
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
        "prime_agent.web.routes.harnesses.get_settings",
        "prime_agent.services.harness_bind_service.get_settings",
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
    return await make_user(email="bind@example.com", name="Bind User")


@pytest.fixture
async def harness(db_session, owner):
    return await HarnessService.acreate(
        {
            "name": "bind-harness",
            "model": "claude-sonnet-4",
            "mode": "agent",
            "tools": [],
            "memory": [],
            "user_id": owner.id,
            "created_by": owner.id,
        },
        db_session,
    )


def _offered_payload(status: str = "offered") -> dict:
    return {
        "kind": "composition",
        "artifact_id": "githi-v1",
        "digest": DIGEST,
        "status": status,
        "semver": GITHI["semver"],
        "body": GITHI,
    }


def _catalog(request: httpx.Request) -> httpx.Response:
    return catalog_response(request) or httpx.Response(404)


def _patch_bind(monkeypatch, settings: Settings, handler):
    def make_client(s=None):
        return AiClient(s or settings, transport=httpx.MockTransport(handler))

    monkeypatch.setattr(
        "prime_agent.services.harness_bind_service.AiClient.from_settings",
        make_client,
    )
    monkeypatch.setattr(
        "prime_agent.web.routes.harnesses.HarnessBindService.from_settings",
        lambda s=None: HarnessBindService(settings, client=make_client(settings)),
    )


@pytest.mark.asyncio
async def test_bind_unauthenticated_returns_401_before_catalog_call(
    db_session, bind_settings, monkeypatch
):
    called = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        called["n"] += 1
        return httpx.Response(200, json=_offered_payload())

    _patch_bind(monkeypatch, bind_settings, handler)
    app = create_app()

    async def override_db():
        yield db_session

    app.dependency_overrides[get_db] = override_db
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as api:
        response = await api.post("/api/v1/harnesses/any-id/bind", json={})
    assert response.status_code == 401
    assert called["n"] == 0


@pytest.mark.asyncio
async def test_bind_valid_shipped_composition_sets_bound_and_digests(
    db_session, owner, harness, bind_settings, monkeypatch
):
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.setdefault("paths", []).append(request.url.path)
        return _catalog(request)

    _patch_bind(monkeypatch, bind_settings, handler)
    app = create_app()

    async def override_db():
        yield db_session

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = lambda: owner
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as api:
        response = await api.post(f"/api/v1/harnesses/{harness.id}/bind", json={})

    assert response.status_code == 200
    body = response.json()
    assert body["bind"]["bind_status"] == "bound"
    # Nothing typed into the backend: the digest is the offered one, the harness
    # version comes from the composition and the runtime from that version.
    assert body["bind"]["composition_id"] == "githi-v1"
    assert body["bind"]["composition_digest"] == DIGEST
    assert body["bind"]["harness_version"] == PIN["harness_version"]
    assert body["bind"]["runtime"] == PIN["runtime"]
    assert any(p.endswith("/v1/catalog/compositions") for p in captured["paths"])
    assert any(p.endswith("/v1/harness_versions/githi-v1") for p in captured["paths"])
    # Bind must commit — get_db rolls back uncommitted sessions; re-load must stay bound.
    await db_session.refresh(harness)
    assert harness.bind_status == "bound"
    assert harness.composition_digest == DIGEST
    persisted = await HarnessService.aget_one(harness.id, db_session)
    assert persisted.bind_status == "bound"
    assert persisted.composition_id == "githi-v1"
    assert persisted.composition_digest == DIGEST


@pytest.mark.asyncio
async def test_bind_unknown_digest_returns_422_composition_not_offered(
    db_session, owner, harness, bind_settings, monkeypatch
):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"detail": "DIGEST_MISSING"})

    _patch_bind(monkeypatch, bind_settings, handler)
    app = create_app()

    async def override_db():
        yield db_session

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = lambda: owner
    transport = httpx.ASGITransport(app=app)
    bad = "sha256:" + ("a" * 64)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as api:
        response = await api.post(
            f"/api/v1/harnesses/{harness.id}/bind",
            json={"composition_digest": bad},
        )
    assert response.status_code == 422
    assert response.json()["detail"]["error_code"] == "COMPOSITION_NOT_OFFERED"


@pytest.mark.asyncio
async def test_bind_floating_latest_rejected(db_session, owner, harness, bind_settings):
    app = create_app()

    async def override_db():
        yield db_session

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = lambda: owner
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as api:
        response = await api.post(
            f"/api/v1/harnesses/{harness.id}/bind",
            json={"composition_id": "githi-v1:latest"},
        )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_bind_deprecated_composition_returns_422_composition_not_offered(
    db_session, owner, harness, bind_settings, monkeypatch
):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/v1/catalog/compositions"):
            return httpx.Response(200, json=[])  # deprecated: no longer listed as offered
        return httpx.Response(200, json=_offered_payload(status="deprecated"))

    _patch_bind(monkeypatch, bind_settings, handler)
    app = create_app()

    async def override_db():
        yield db_session

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = lambda: owner
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as api:
        response = await api.post(f"/api/v1/harnesses/{harness.id}/bind", json={})
    assert response.status_code == 422
    assert response.json()["detail"]["error_code"] == "COMPOSITION_NOT_OFFERED"


@pytest.mark.asyncio
async def test_bind_other_users_harness_returns_404(
    db_session, owner, harness, make_user, bind_settings, monkeypatch
):
    other = await make_user(email="other-bind@example.com", name="Other")
    called = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        called["n"] += 1
        return httpx.Response(200, json=_offered_payload())

    _patch_bind(monkeypatch, bind_settings, handler)
    app = create_app()

    async def override_db():
        yield db_session

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = lambda: other
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as api:
        response = await api.post(f"/api/v1/harnesses/{harness.id}/bind", json={})
    assert response.status_code == 404
    assert called["n"] == 0


@pytest.mark.asyncio
async def test_bind_catalog_unreachable_returns_503_catalog_unavailable(
    db_session, owner, harness, bind_settings, monkeypatch
):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom", request=request)

    _patch_bind(monkeypatch, bind_settings, handler)
    app = create_app()

    async def override_db():
        yield db_session

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = lambda: owner
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as api:
        response = await api.post(f"/api/v1/harnesses/{harness.id}/bind", json={})
    assert response.status_code == 503
    assert response.json()["detail"]["error_code"] == "CATALOG_UNAVAILABLE"


@pytest.mark.asyncio
async def test_bind_uses_service_bearer_not_user_access_token(
    db_session, owner, harness, bind_settings, monkeypatch
):
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["authorization"] = request.headers.get("authorization", "")
        return _catalog(request)

    _patch_bind(monkeypatch, bind_settings, handler)
    app = create_app()

    async def override_db():
        yield db_session

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = lambda: owner
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as api:
        await api.post(f"/api/v1/harnesses/{harness.id}/bind", json={})

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
async def test_ensure_runnable_pin_unbound_raises_not_bound(
    db_session, owner, harness, bind_settings
):
    service = HarnessBindService(bind_settings, client=AiClient(bind_settings))
    with pytest.raises(NotBoundError):
        await service.ensure_runnable_pin(
            harness_id=harness.id,
            user_id=owner.id,
            identity=TenantPrincipal.from_user(owner),
            db=db_session,
            require_run_allowed=False,
        )


@pytest.mark.asyncio
async def test_ensure_runnable_pin_stale_raises_stale_bind(
    db_session, owner, harness, bind_settings, monkeypatch
):
    harness.bind_status = "stale"
    harness.composition_id = PIN["composition_id"]
    harness.composition_digest = DIGEST
    harness.harness_version = PIN["harness_version"]
    harness.runtime = PIN["runtime"]
    await db_session.flush()

    service = HarnessBindService(bind_settings)
    with pytest.raises(StaleBindError):
        await service.ensure_runnable_pin(
            harness_id=harness.id,
            user_id=owner.id,
            identity=TenantPrincipal.from_user(owner),
            db=db_session,
            require_run_allowed=False,
        )


@pytest.mark.asyncio
async def test_ensure_runnable_pin_when_ai_not_ready_raises_ai_platform_not_ready(
    db_session, owner, harness, bind_settings, monkeypatch
):
    def handler(request: httpx.Request) -> httpx.Response:
        if "readyz" in request.url.path:
            return httpx.Response(503, json={"status": "not_ready", "reason": "draining"})
        return httpx.Response(200, json=_offered_payload())

    client = AiClient(bind_settings, transport=httpx.MockTransport(handler))
    service = HarnessBindService(bind_settings, client=client)

    harness.bind_status = "bound"
    harness.composition_id = PIN["composition_id"]
    harness.composition_digest = DIGEST
    harness.harness_version = PIN["harness_version"]
    harness.runtime = PIN["runtime"]
    await db_session.flush()

    from prime_agent.services.ai_client import AiPlatformNotReadyError

    with pytest.raises(AiPlatformNotReadyError):
        await service.ensure_runnable_pin(
            harness_id=harness.id,
            user_id=owner.id,
            identity=TenantPrincipal.from_user(owner),
            db=db_session,
            require_run_allowed=True,
        )


@pytest.mark.asyncio
async def test_harness_get_includes_unbound_bind_view_by_default(
    db_session, owner, harness, bind_settings
):
    app = create_app()

    async def override_db():
        yield db_session

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = lambda: owner
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as api:
        response = await api.get(f"/api/v1/harnesses/{harness.id}")
    assert response.status_code == 200
    assert response.json()["bind"]["bind_status"] == "unbound"


@pytest.mark.asyncio
async def test_product_crud_still_works_for_unbound_draft(
    db_session, owner, bind_settings
):
    created = await HarnessService.acreate(
        {
            "name": "draft-only",
            "model": "gpt-4o",
            "mode": "chat",
            "tools": [],
            "memory": [],
            "user_id": owner.id,
            "created_by": owner.id,
        },
        db_session,
    )
    assert created.bind_status == "unbound"
    assert created.composition_digest is None


@pytest.mark.asyncio
async def test_bind_picks_the_newest_offered_version_of_a_composition(
    db_session, owner, harness, bind_settings
):
    newer = "sha256:" + "b" * 64

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/v1/catalog/compositions"):
            return httpx.Response(200, json=[
                {"kind": "composition", "artifact_id": "githi-v1", "digest": DIGEST, "status": "offered", "semver": "1.1.0"},
                {"kind": "composition", "artifact_id": "githi-v1", "digest": newer, "status": "offered", "semver": "1.10.0"},
                {"kind": "composition", "artifact_id": "other", "digest": "sha256:" + "c" * 64, "status": "offered", "semver": "9.0.0"},
            ])
        if "/v1/catalog/composition/githi-v1/" in request.url.path:
            return httpx.Response(200, json=_offered_payload() | {"digest": newer, "semver": "1.10.0"})
        return _catalog(request)

    service = HarnessBindService(bind_settings, client=AiClient(bind_settings, transport=httpx.MockTransport(handler)))
    bound = await service.bind_to_composition(
        harness_id=harness.id,
        user_id=owner.id,
        identity=TenantPrincipal.from_user(owner),
        request=HarnessBindRequest(),
        db=db_session,
    )
    assert bound.bind.composition_digest == newer


@pytest.mark.asyncio
async def test_bind_refusal_from_the_catalog_names_the_reason(
    db_session, owner, harness, bind_settings
):
    """A custom tool the supervisor cannot run: the user sees why, not "catalog unavailable"."""
    from prime_agent.db.models.tool import Tool

    tool = Tool(name="weather", description="w", category="custom", parameters=[],
                endpoint="https://api.example.com/weather", user_id=owner.id, created_by=owner.id)
    db_session.add(tool)
    await db_session.flush()
    harness.tools = [tool.id]
    await db_session.flush()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/v1/catalog/tool_set/publish"):
            return httpx.Response(422, json={"detail": {"message": "unknown field(s): definitions.weather.http_url", "code": "INVALID_ARTIFACT"}})
        return _catalog(request)

    service = HarnessBindService(bind_settings, client=AiClient(bind_settings, transport=httpx.MockTransport(handler)))
    from prime_agent.services.harness_bind_service import ToolPublishBindError

    with pytest.raises(ToolPublishBindError, match="http_url"):
        await service.bind_to_composition(
            harness_id=harness.id,
            user_id=owner.id,
            identity=TenantPrincipal.from_user(owner),
            request=HarnessBindRequest(),
            db=db_session,
        )
