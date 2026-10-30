"""Production D4 session BFF tests."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from uuid import uuid4

import httpx
import jwt
import pytest

from prime_agent.config import Settings, get_settings
from prime_agent.db.models.session_link import SessionLink
from prime_agent.main import create_app
from prime_agent.services.ai_client import AiClient
from prime_agent.services.harness_service import HarnessService
from prime_agent.services.platform_status_service import PlatformStatusService
from prime_agent.services.session_service import SessionService
from prime_agent.web.dependencies import get_current_user, get_db
from tests.support.catalog import DIGEST

SECRET = "ai-service-jwt-secret-at-least-32-bytes!!"


@pytest.fixture
def session_settings(monkeypatch):
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
        "prime_agent.web.routes.sessions.get_settings",
        "prime_agent.services.session_service.get_settings",
        "prime_agent.services.harness_bind_service.get_settings",
        "prime_agent.services.platform_status_service.get_settings",
        "prime_agent.services.ai_client.get_settings",
        "prime_agent.security.service_jwt.get_settings",
    ):
        monkeypatch.setattr(target, lambda: settings)
    yield settings
    get_settings.cache_clear()
    PlatformStatusService.clear_cache()


@pytest.fixture
async def owner(db_session, make_user):
    return await make_user(email="sessions@example.com", name="Session User")


@pytest.fixture
async def harness(db_session, owner):
    h = await HarnessService.acreate(
        {
            "name": "session-harness",
            "model": "claude-sonnet-4",
            "mode": "agent",
            "tools": [],
            "memory": [],
            "user_id": owner.id,
            "created_by": owner.id,
        },
        db_session,
    )
    h.bind_status = "bound"
    h.harness_version = "githi-v1"
    h.runtime = "githi"
    h.composition_id = "githi-v1"
    h.composition_digest = DIGEST
    h.bound_at = datetime.now(UTC)
    h.bound_by = owner.id
    await db_session.flush()
    return h


def _catalog_ok(request: httpx.Request) -> httpx.Response | None:
    if "/v1/catalog/composition/" in request.url.path:
        return httpx.Response(
            200,
            json={
                "kind": "composition",
                "artifact_id": "githi-v1",
                "digest": DIGEST,
                "status": "offered",
                "semver": "1.0.0",
            },
        )
    if request.url.path.endswith("/readyz"):
        return httpx.Response(200, json={"status": "ready"})
    return None


def _patch_session(monkeypatch, settings: Settings, handler):
    def make_client(s=None):
        return AiClient(s or settings, transport=httpx.MockTransport(handler))

    monkeypatch.setattr(
        "prime_agent.services.session_service.AiClient.from_settings",
        make_client,
    )
    monkeypatch.setattr(
        "prime_agent.services.harness_bind_service.AiClient.from_settings",
        make_client,
    )
    monkeypatch.setattr(
        "prime_agent.web.routes.sessions.SessionService.from_settings",
        lambda s=None: SessionService(settings, client=make_client(settings)),
    )


@pytest.mark.asyncio
async def test_create_session_unauthenticated_returns_401_before_upstream(
    db_session, session_settings, monkeypatch
):
    called = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        called["n"] += 1
        return httpx.Response(201, json={"session_id": "x"})

    _patch_session(monkeypatch, session_settings, handler)
    app = create_app()

    async def override_db():
        yield db_session

    app.dependency_overrides[get_db] = override_db
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as api:
        r = await api.post("/api/v1/sessions", json={"harness_id": "h1"})
    assert r.status_code == 401
    assert called["n"] == 0


@pytest.mark.asyncio
async def test_create_session_digest_fields_rejected(
    db_session, owner, harness, session_settings
):
    app = create_app()

    async def override_db():
        yield db_session

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = lambda: owner
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as api:
        r = await api.post(
            "/api/v1/sessions",
            json={
                "harness_id": harness.id,
                "composition_digest": DIGEST,
            },
        )
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_create_session_unbound_returns_422_not_bound(
    db_session, owner, session_settings, monkeypatch
):
    unbound = await HarnessService.acreate(
        {
            "name": "unbound-h",
            "model": "gpt-4o",
            "mode": "chat",
            "tools": [],
            "memory": [],
            "user_id": owner.id,
            "created_by": owner.id,
        },
        db_session,
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"status": "ready"})

    _patch_session(monkeypatch, session_settings, handler)
    app = create_app()

    async def override_db():
        yield db_session

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = lambda: owner
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as api:
        r = await api.post("/api/v1/sessions", json={"harness_id": unbound.id})
    assert r.status_code == 422
    assert r.json()["detail"]["error_code"] == "NOT_BOUND"


@pytest.mark.asyncio
async def test_create_session_when_ai_not_ready_returns_503_ai_platform_not_ready(
    db_session, owner, harness, session_settings, monkeypatch
):
    def handler(request: httpx.Request) -> httpx.Response:
        if "/readyz" in request.url.path:
            return httpx.Response(503, json={"status": "not_ready", "reason": "draining"})
        cat = _catalog_ok(request)
        if cat:
            return cat
        return httpx.Response(500, json={})

    _patch_session(monkeypatch, session_settings, handler)
    app = create_app()

    async def override_db():
        yield db_session

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = lambda: owner
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as api:
        r = await api.post("/api/v1/sessions", json={"harness_id": harness.id})
    assert r.status_code == 503
    assert r.json()["detail"]["error_code"] == "AI_PLATFORM_NOT_READY"


@pytest.mark.asyncio
async def test_create_session_uses_ensure_runnable_pin_and_service_bearer_only(
    db_session, owner, harness, session_settings, monkeypatch
):
    captured: dict[str, object] = {"bodies": [], "auths": []}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["auths"].append(request.headers.get("authorization", ""))
        cat = _catalog_ok(request)
        if cat:
            return cat
        if request.method == "POST" and request.url.path.endswith("/v1/sessions"):
            body = json.loads(request.content.decode())
            captured["bodies"].append(body)
            return httpx.Response(
                201,
                json={
                    "session_id": "ai-sess-1",
                    "harness_version": body["harness_version"],
                    "runtime": body["runtime"],
                    "status": "active",
                },
            )
        return httpx.Response(404)

    _patch_session(monkeypatch, session_settings, handler)
    app = create_app()

    async def override_db():
        yield db_session

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = lambda: owner
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as api:
        r = await api.post("/api/v1/sessions", json={"harness_id": harness.id})
    assert r.status_code == 201
    body = r.json()
    assert body["id"]
    assert body["pin"]["composition_digest"] == DIGEST
    assert captured["bodies"][0]["harness_version"] == "githi-v1"
    assert captured["bodies"][0]["composition_digest"] == DIGEST
    token = str(captured["auths"][-1]).removeprefix("Bearer ")
    payload = jwt.decode(
        token,
        SECRET,
        algorithms=["HS256"],
        audience="yal-supervisor",
        issuer="prime-agent-api",
    )
    assert payload["type"] == "service"


@pytest.mark.asyncio
async def test_create_session_inserts_ownership_row_with_pin_snapshot(
    db_session, owner, harness, session_settings, monkeypatch
):
    def handler(request: httpx.Request) -> httpx.Response:
        cat = _catalog_ok(request)
        if cat:
            return cat
        if request.method == "POST" and request.url.path.endswith("/v1/sessions"):
            return httpx.Response(
                201,
                json={
                    "session_id": "ai-own-1",
                    "harness_version": "githi-v1",
                    "runtime": "githi",
                    "status": "active",
                },
            )
        return httpx.Response(404)

    _patch_session(monkeypatch, session_settings, handler)
    app = create_app()

    async def override_db():
        yield db_session

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = lambda: owner
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as api:
        r = await api.post("/api/v1/sessions", json={"harness_id": harness.id})
    assert r.status_code == 201
    sid = r.json()["id"]
    from sqlalchemy import select

    # The request's database session closes without a commit of its own: a link
    # that was only flushed is gone, and every later call on the session 404s.
    owner_id = owner.id  # a rollback expires loaded objects
    await db_session.rollback()
    row = (
        await db_session.execute(select(SessionLink).where(SessionLink.id == sid))
    ).scalar_one()
    assert row.ai_session_id == "ai-own-1"
    assert row.composition_digest == DIGEST
    assert row.user_id == owner_id


@pytest.mark.asyncio
async def test_message_mints_command_id_and_maps_turn_id(
    db_session, owner, harness, session_settings, monkeypatch
):
    link = SessionLink(
        id=str(uuid4()),
        user_id=owner.id,
        harness_id=harness.id,
        ai_session_id="ai-msg-1",
        tenant_id=owner.id,
        harness_version="githi-v1",
        runtime="githi",
        composition_id="githi-v1",
        composition_digest=DIGEST,
        status="active",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    db_session.add(link)
    await db_session.flush()
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        cat = _catalog_ok(request)
        if cat:
            return cat
        if request.url.path.endswith("/messages"):
            body = json.loads(request.content.decode())
            seen["command_id"] = body["command_id"]
            seen["text"] = body["text"]
            seen["steer"] = body.get("steer", False)
            if body.get("steer"):  # joins the running turn
                return httpx.Response(202, json={"turn_id": "turn-9", "status": "accepted", "steered": True})
            if body["text"] == "nobody is up":  # no worker: kept, runs once one comes
                return httpx.Response(202, json={"turn_id": "turn-9", "status": "accepted", "queued": True, "held": True})
            return httpx.Response(202, json={"turn_id": "turn-9", "status": "accepted", "queued": True})
        return httpx.Response(404)

    _patch_session(monkeypatch, session_settings, handler)
    app = create_app()

    async def override_db():
        yield db_session

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = lambda: owner
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as api:
        r = await api.post(
            f"/api/v1/sessions/{link.id}/messages",
            json={"content": "hello"},
        )
        assert seen["text"] == "hello" and seen["steer"] is False
        assert seen["command_id"] == r.json()["command_id"]
        steered = await api.post(
            f"/api/v1/sessions/{link.id}/messages",
            json={"content": "look at this instead", "steer": True},
        )
        steer_seen = seen["text"], seen["steer"]
        held = await api.post(f"/api/v1/sessions/{link.id}/messages", json={"content": "nobody is up"})
    assert r.status_code == 202
    assert r.json()["turn_id"] == "turn-9"
    assert r.json()["queued"] is True  # behind a running turn: the UI can say so
    assert r.json()["command_id"]
    assert steer_seen == ("look at this instead", True)
    assert steered.json()["steered"] is True and steered.json()["turn_id"] == "turn-9"
    assert r.json()["steered"] is False
    assert held.json()["held"] is True and r.json()["held"] is False


@pytest.mark.asyncio
async def test_get_message_events_abort_delete_fork_other_users_session_returns_404(
    db_session, owner, harness, make_user, session_settings, monkeypatch
):
    other = await make_user(email="other-sess@example.com", name="Other")
    link = SessionLink(
        id=str(uuid4()),
        user_id=owner.id,
        harness_id=harness.id,
        ai_session_id="ai-idor",
        tenant_id=owner.id,
        harness_version="githi-v1",
        runtime="githi",
        status="active",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    db_session.add(link)
    await db_session.flush()
    called = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        called["n"] += 1
        return httpx.Response(200, json={"status": "ok"})

    _patch_session(monkeypatch, session_settings, handler)
    app = create_app()

    async def override_db():
        yield db_session

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = lambda: other
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as api:
        assert (await api.get(f"/api/v1/sessions/{link.id}")).status_code == 404
        assert (
            await api.post(
                f"/api/v1/sessions/{link.id}/messages", json={"content": "x"}
            )
        ).status_code == 404
        assert (await api.get(f"/api/v1/sessions/{link.id}/events")).status_code == 404
        assert (await api.post(f"/api/v1/sessions/{link.id}/abort")).status_code == 404
        assert (await api.delete(f"/api/v1/sessions/{link.id}")).status_code == 404
        assert (await api.post(f"/api/v1/sessions/{link.id}/fork")).status_code == 404
    assert called["n"] == 0


@pytest.mark.asyncio
async def test_sse_proxy_forwards_last_event_id_and_content_type(
    db_session, owner, harness, session_settings, monkeypatch
):
    link = SessionLink(
        id=str(uuid4()),
        user_id=owner.id,
        harness_id=harness.id,
        ai_session_id="ai-sse",
        tenant_id=owner.id,
        harness_version="githi-v1",
        runtime="githi",
        status="active",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    db_session.add(link)
    await db_session.flush()
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if "/events" in request.url.path:
            seen["last"] = request.headers.get("last-event-id", "")
            return httpx.Response(
                200,
                content=b"id: 3\ndata: {\"seq\":3,\"type\":\"run_end\"}\n\n",
                headers={"content-type": "text/event-stream"},
            )
        return httpx.Response(404)

    _patch_session(monkeypatch, session_settings, handler)
    app = create_app()

    async def override_db():
        yield db_session

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = lambda: owner
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as api:
        r = await api.get(
            f"/api/v1/sessions/{link.id}/events",
            headers={"Last-Event-ID": "2"},
        )
    assert r.status_code == 200
    assert "text/event-stream" in r.headers.get("content-type", "")
    assert seen["last"] == "2"
    assert b"run_end" in r.content


@pytest.mark.asyncio
async def test_abort_maps_to_supervisor_abort(
    db_session, owner, harness, session_settings, monkeypatch
):
    link = SessionLink(
        id=str(uuid4()),
        user_id=owner.id,
        harness_id=harness.id,
        ai_session_id="ai-abort",
        tenant_id=owner.id,
        harness_version="githi-v1",
        runtime="githi",
        status="active",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    db_session.add(link)
    await db_session.flush()
    seen = {"path": ""}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        return httpx.Response(200, json={"status": "ok"})

    _patch_session(monkeypatch, session_settings, handler)
    app = create_app()

    async def override_db():
        yield db_session

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = lambda: owner
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as api:
        r = await api.post(f"/api/v1/sessions/{link.id}/abort")
    assert r.status_code == 200
    assert seen["path"].endswith("/abort")


@pytest.mark.asyncio
async def test_delete_maps_to_supervisor_terminate(
    db_session, owner, harness, session_settings, monkeypatch
):
    link = SessionLink(
        id=str(uuid4()),
        user_id=owner.id,
        harness_id=harness.id,
        ai_session_id="ai-term",
        tenant_id=owner.id,
        harness_version="githi-v1",
        runtime="githi",
        status="active",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    db_session.add(link)
    await db_session.flush()
    seen = {"method": ""}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        return httpx.Response(204)

    _patch_session(monkeypatch, session_settings, handler)
    app = create_app()

    async def override_db():
        yield db_session

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = lambda: owner
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as api:
        r = await api.delete(f"/api/v1/sessions/{link.id}")
    assert r.status_code == 204
    assert seen["method"] == "DELETE"


@pytest.mark.asyncio
@pytest.mark.parametrize("upstream", [204, 404], ids=["erased-now", "already-erased-upstream"])
async def test_erase_erases_upstream_then_forgets_the_link(
    db_session, owner, harness, session_settings, monkeypatch, upstream
):
    link = SessionLink(
        id=str(uuid4()),
        user_id=owner.id,
        harness_id=harness.id,
        ai_session_id="ai-erase",
        tenant_id=owner.id,
        harness_version="githi-v1",
        runtime="githi",
        status="active",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    db_session.add(link)
    await db_session.flush()
    seen: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path))
        return httpx.Response(upstream, json={} if upstream == 404 else None)

    _patch_session(monkeypatch, session_settings, handler)
    app = create_app()

    async def override_db():
        yield db_session

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = lambda: owner
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as api:
        r = await api.delete(f"/api/v1/sessions/{link.id}/data")
        again = await api.delete(f"/api/v1/sessions/{link.id}/data")
    assert r.status_code == 204
    assert seen == [("DELETE", "/v1/sessions/ai-erase/data")]
    assert await db_session.get(SessionLink, link.id) is None
    assert again.status_code == 404  # the product no longer knows it


@pytest.mark.asyncio
async def test_pause_resume_return_501(db_session, owner, session_settings):
    app = create_app()

    async def override_db():
        yield db_session

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = lambda: owner
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as api:
        assert (await api.post("/api/v1/sessions/x/pause")).status_code == 501
        assert (await api.post("/api/v1/sessions/x/resume")).status_code == 501


@pytest.mark.asyncio
async def test_list_includes_cost_from_detail_projection(
    db_session, owner, harness, session_settings, monkeypatch
):
    link = SessionLink(
        id=str(uuid4()),
        user_id=owner.id,
        harness_id=harness.id,
        ai_session_id="ai-cost",
        tenant_id=owner.id,
        harness_version="githi-v1",
        runtime="githi",
        status="active",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    db_session.add(link)
    await db_session.flush()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/ai-cost"):
            return httpx.Response(
                200,
                json={
                    "session_id": "ai-cost",
                    "status": "active",
                    "harness_version": "githi-v1",
                    "runtime": "githi",
                    "last_seq": 4,
                    "message_count": 2,
                    "total_cost": 1.25,
                },
            )
        return httpx.Response(404)

    _patch_session(monkeypatch, session_settings, handler)
    app = create_app()

    async def override_db():
        yield db_session

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = lambda: owner
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as api:
        r = await api.get("/api/v1/sessions")
    assert r.status_code == 200
    assert r.json()["data"][0]["total_cost"] == 1.25


@pytest.mark.asyncio
async def test_create_session_shows_why_the_sandbox_cannot_start(
    db_session, owner, harness, session_settings, monkeypatch
):
    reason = "Docker is not reachable, so this harness's sandbox (Docker container) cannot start."

    def handler(request: httpx.Request) -> httpx.Response:
        cat = _catalog_ok(request)
        if cat:
            return cat
        if request.method == "POST" and request.url.path.endswith("/v1/sessions"):
            return httpx.Response(422, json={"detail": reason})
        return httpx.Response(404)

    _patch_session(monkeypatch, session_settings, handler)
    app = create_app()

    async def override_db():
        yield db_session

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = lambda: owner
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as api:
        r = await api.post("/api/v1/sessions", json={"harness_id": harness.id})
    assert r.status_code == 422
    assert r.json()["detail"]["message"] == reason  # the supervisor's reason, not "unreachable"


@pytest.mark.asyncio
async def test_create_session_timeout_says_so(db_session, owner, harness, session_settings, monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        cat = _catalog_ok(request)
        if cat:
            return cat
        raise httpx.ReadTimeout("slow", request=request)

    _patch_session(monkeypatch, session_settings, handler)
    app = create_app()

    async def override_db():
        yield db_session

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = lambda: owner
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as api:
        r = await api.post("/api/v1/sessions", json={"harness_id": harness.id})
    assert r.status_code == 503
    assert r.json()["detail"]["message"] == "AI supervisor did not answer in time"


@pytest.mark.asyncio
async def test_create_answers_at_once_and_startup_reports_how_it_goes(
    db_session, owner, harness, session_settings, monkeypatch
):
    prefer: list[str | None] = []
    phase = {"now": "starting"}

    def handler(request: httpx.Request) -> httpx.Response:
        cat = _catalog_ok(request)
        if cat:
            return cat
        if request.method == "POST" and request.url.path.endswith("/v1/sessions"):
            prefer.append(request.headers.get("prefer"))
            return httpx.Response(202, json={"session_id": "ai-async-1", "harness_version": "githi-v1",
                                             "runtime": "githi", "status": "starting"})
        if request.url.path.endswith("/startup"):
            if phase["now"] == "starting":
                return httpx.Response(200, json={"session_id": "ai-async-1", "status": "starting",
                                                 "worker_available": False, "reason": "No agent worker is running"})
            return httpx.Response(200, json={"session_id": "ai-async-1", "status": "failed", "worker_available": True,
                                             "reason": "Docker is not reachable", "kind": "SandboxUnavailable"})
        return httpx.Response(404)

    _patch_session(monkeypatch, session_settings, handler)
    app = create_app()

    async def override_db():
        yield db_session

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = lambda: owner
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as api:
        created = await api.post("/api/v1/sessions", json={"harness_id": harness.id})
        sid = created.json()["id"]
        waiting = await api.get(f"/api/v1/sessions/{sid}/startup")
        phase["now"] = "failed"
        failed = await api.get(f"/api/v1/sessions/{sid}/startup")
        after = await api.get(f"/api/v1/sessions/{sid}")
    assert created.status_code == 201 and created.json()["status"] == "starting"
    assert prefer == ["respond-async"]
    assert waiting.json() == {"status": "starting", "worker_available": False,
                              "reason": "No agent worker is running", "kind": None}
    assert failed.json()["status"] == "failed" and failed.json()["reason"] == "Docker is not reachable"
    assert after.json()["status"] == "failed"  # recorded on the session


@pytest.mark.asyncio
async def test_runs_pass_through_with_202_until_ended_and_cancel(
    db_session, owner, harness, session_settings, monkeypatch
):
    run = {"run_id": "r1", "command_id": "c1", "status": "in_progress", "created_at_ms": 1, "started_at_ms": 2}
    cancels: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        cat = _catalog_ok(request)
        if cat:
            return cat
        path = request.url.path
        if request.method == "POST" and path.endswith("/v1/sessions"):
            return httpx.Response(201, json={"session_id": "ai-runs-1", "harness_version": "githi-v1",
                                             "runtime": "githi", "status": "ready"})
        if path.endswith("/ai-runs-1/runs") and request.method == "GET":
            assert request.url.params["limit"] == "5"
            return httpx.Response(200, json=[run])
        if path.endswith("/ai-runs-1/runs/r1") and request.method == "GET":
            return httpx.Response(202 if run["status"] == "in_progress" else 200, json=run)
        if path.endswith("/ai-runs-1/runs/r1/cancel"):
            cancels.append(path)
            run.update(status="cancelled", stop_reason="aborted", ended_at_ms=3)
            return httpx.Response(200, json=run)
        if path.endswith("/runs/nope"):
            return httpx.Response(404, json={"detail": "run not found"})
        return httpx.Response(404)

    _patch_session(monkeypatch, session_settings, handler)
    app = create_app()

    async def override_db():
        yield db_session

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = lambda: owner
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as api:
        sid = (await api.post("/api/v1/sessions", json={"harness_id": harness.id})).json()["id"]
        going = await api.get(f"/api/v1/sessions/{sid}/runs/r1")
        listed = await api.get(f"/api/v1/sessions/{sid}/runs?limit=5")
        stopped = await api.post(f"/api/v1/sessions/{sid}/runs/r1/cancel")
        again = await api.post(f"/api/v1/sessions/{sid}/runs/r1/cancel")
        ended = await api.get(f"/api/v1/sessions/{sid}/runs/r1")
        missing = await api.get(f"/api/v1/sessions/{sid}/runs/nope")
        stranger = await api.get("/api/v1/sessions/not-mine/runs/r1")
    assert going.status_code == 202 and going.json()["status"] == "in_progress"
    assert [r["run_id"] for r in listed.json()] == ["r1"]
    assert stopped.status_code == 200 and stopped.json()["status"] == "cancelled"
    assert again.status_code == 200 and again.json() == stopped.json()
    assert ended.status_code == 200 and ended.json()["stop_reason"] == "aborted"
    assert missing.status_code == 404
    assert stranger.status_code == 404


@pytest.mark.asyncio
async def test_a_message_longer_than_a_session_takes_is_refused_before_the_supervisor(
    db_session, owner, harness, session_settings, monkeypatch
):
    from prime_agent.schemas.session import MAX_MESSAGE_CHARS

    posted: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        cat = _catalog_ok(request)
        if cat:
            return cat
        if request.method == "POST" and request.url.path.endswith("/v1/sessions"):
            return httpx.Response(201, json={"session_id": "ai-big-1", "harness_version": "githi-v1",
                                             "runtime": "githi", "status": "ready"})
        if request.url.path.endswith("/messages"):
            posted.append(request.url.path)
            return httpx.Response(202, json={"turn_id": "t", "command_id": "c", "status": "accepted"})
        return httpx.Response(404)

    _patch_session(monkeypatch, session_settings, handler)
    app = create_app()

    async def override_db():
        yield db_session

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = lambda: owner
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as api:
        sid = (await api.post("/api/v1/sessions", json={"harness_id": harness.id})).json()["id"]
        over = await api.post(f"/api/v1/sessions/{sid}/messages", json={"content": "x" * (MAX_MESSAGE_CHARS + 1)})
        at = await api.post(f"/api/v1/sessions/{sid}/messages", json={"content": "x" * MAX_MESSAGE_CHARS})
    assert over.status_code == 422
    assert at.status_code in (200, 201, 202), at.text
    assert len(posted) == 1  # only the one within the limit reached the supervisor
