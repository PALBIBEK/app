"""Views across the user's sessions and the operating pages, through the BFF.

The supervisor is a mock: these check that its session ids become this
product's ids, that another user's sessions never show, that names are checked
before they reach it, and that the fleet pages are for admins only.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from uuid import uuid4

import httpx
import pytest

from prime_agent.config import Settings, get_settings
from prime_agent.db.models.session_link import SessionLink
from prime_agent.main import create_app
from prime_agent.services.ai_client import AiClient
from prime_agent.services.harness_service import HarnessService
from prime_agent.web.dependencies import get_current_user, get_db
from prime_agent.web.routes import catalog as catalog_routes
from prime_agent.web.routes import operate

SECRET = "ai-service-jwt-secret-at-least-32-bytes!!"


@pytest.fixture
def settings(monkeypatch):
    s = Settings(
        environment="development",
        secret_key="user-secret-key-also-at-least-32-bytes!!",
        ai_supervisor_base_url="http://supervisor.test",
        ai_service_jwt_secret=SECRET,
        ai_platform_status_cache_ttl_seconds=0,
    )
    get_settings.cache_clear()
    for target in (
        "prime_agent.config.get_settings",
        "prime_agent.config.settings.get_settings",
        "prime_agent.services.ai_client.get_settings",
        "prime_agent.security.service_jwt.get_settings",
        "prime_agent.web.routes.catalog.get_settings",
        "prime_agent.web.routes.operate.get_settings",
    ):
        monkeypatch.setattr(target, lambda: s)
    yield s
    get_settings.cache_clear()


@pytest.fixture
async def owner(make_user):
    return await make_user(email="ops@example.com", name="Ops User")


@pytest.fixture
async def link(db_session, owner):
    h = await HarnessService.acreate(
        {
            "name": "research-agent",
            "model": "claude-sonnet-4",
            "mode": "agent",
            "tools": [],
            "memory": [],
            "user_id": owner.id,
            "created_by": owner.id,
        },
        db_session,
    )
    row = SessionLink(
        id=str(uuid4()),
        user_id=owner.id,
        harness_id=h.id,
        ai_session_id="ai-1",
        tenant_id=owner.id,
        harness_version="githi-v1",
        runtime="githi",
        composition_id="comp-research",
        status="active",
        title="Quarterly research",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    db_session.add(row)
    await db_session.flush()
    return row


def _api(db_session, user, settings, monkeypatch, handler, seen: list[httpx.Request]) -> httpx.AsyncClient:
    def wrapped(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    def client() -> AiClient:
        return AiClient(settings, transport=httpx.MockTransport(wrapped))

    monkeypatch.setattr(operate, "_client", client)
    monkeypatch.setattr(catalog_routes.AiClient, "from_settings", lambda s=None: client())
    app = create_app()

    async def override_db():
        yield db_session

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = lambda: user
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


@pytest.mark.asyncio
async def test_inbox_and_sandboxes_speak_this_products_session_ids(db_session, owner, link, settings, monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/approvals":
            return httpx.Response(
                200,
                json=[
                    {"approval_id": "ap-1", "session_id": "ai-1", "agent": "comp-research",
                     "prompt": "Write notes.md?", "options": ["allow", "deny"],
                     "created_at_ms": 1, "expires_at_ms": 2},
                    {"approval_id": "ap-2", "session_id": "ai-not-mine", "agent": "x",
                     "prompt": "?", "options": []},
                    # A subagent of the user's session: answered from that session.
                    {"approval_id": "ap-3", "session_id": "ai-child", "root_session_id": "ai-1",
                     "subagent_label": "writer", "agent": "comp-research", "prompt": "Run bash?",
                     "options": ["allow", "deny"]},
                ],
            )
        if request.url.path == "/v1/sandboxes":
            return httpx.Response(
                200,
                json=[{"session_id": "ai-1", "hand": "default", "state": "running", "provider": "local",
                       "sandbox_id": "sb-1", "processes": [], "checkpoint_files": 3, "extra": "ignored"}],
            )
        return httpx.Response(404)

    seen: list[httpx.Request] = []
    async with _api(db_session, owner, settings, monkeypatch, handler, seen) as api:
        inbox = (await api.get("/api/v1/approvals")).json()
        assert inbox == [
            {"approval_id": "ap-1", "session_id": link.id, "session_title": "Quarterly research",
             "harness_id": link.harness_id, "harness_name": "research-agent", "prompt": "Write notes.md?",
             "options": ["allow", "deny"], "created_at_ms": 1, "expires_at_ms": 2,
             "subagent": False, "subagent_label": None},
            {"approval_id": "ap-3", "session_id": link.id, "session_title": "Quarterly research",
             "harness_id": link.harness_id, "harness_name": "research-agent", "prompt": "Run bash?",
             "options": ["allow", "deny"], "created_at_ms": None, "expires_at_ms": None,
             "subagent": True, "subagent_label": "writer"},
        ]

        boxes = (await api.get("/api/v1/sandboxes", params={"session_id": link.id})).json()
        assert [(b["session_id"], b["state"], b["harness_name"], b["checkpoint_files"]) for b in boxes] == [
            (link.id, "running", "research-agent", 3)
        ]
        assert seen[-1].url.params["session_id"] == "ai-1"  # the supervisor's id went upstream
        assert (await api.get("/api/v1/sandboxes", params={"session_id": "someone-elses"})).status_code == 404


@pytest.mark.asyncio
async def test_usage_names_agents_and_sessions(db_session, owner, link, settings, monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        by = request.url.params["by"]
        key = {"agent": "comp-research", "session": "ai-1", "day": "2026-10-20"}[by]
        row = {"key": key, "input_tokens": 10, "output_tokens": 5, "total_tokens": 15, "cost": 0.02,
               "model_calls": 2, "sessions": 1}
        rows = [row] + ([{**row, "key": "ai-not-mine"}] if by == "session" else [])
        if by == "agent":
            rows.append({**row, "key": "h-gone"})  # a composition no agent of the user's has now
        return httpx.Response(200, json={"since_ms": 0, "until_ms": 1, "by": by, "rows": rows,
                                         "total": {**row, "key": "total"}})

    seen: list[httpx.Request] = []
    async with _api(db_session, owner, settings, monkeypatch, handler, seen) as api:
        agent = (await api.get("/api/v1/usage", params={"by": "agent"})).json()
        assert agent["rows"][0]["label"] == "research-agent" and agent["total"]["total_tokens"] == 15
        assert agent["rows"][1]["label"] == "h-gone (removed)"
        session = (await api.get("/api/v1/usage", params={"by": "session"})).json()
        assert [(r["key"], r["label"]) for r in session["rows"]] == [(link.id, "Quarterly research")]
        day = (await api.get("/api/v1/usage")).json()
        assert day["rows"][0]["label"] == "2026-10-20"
        assert (await api.get("/api/v1/usage", params={"by": "week"})).status_code == 422


@pytest.mark.asyncio
async def test_runners_secrets_and_check_pass_through(db_session, owner, settings, monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        path, method = request.url.path, request.method
        if path == "/v1/runners" and method == "GET":
            return httpx.Response(200, json=[{"name": "laptop", "online": True}])
        if path == "/v1/runners/laptop" and method == "POST":
            return httpx.Response(200, json={"name": "laptop", "token": "tok-once"})
        if path == "/v1/runners/laptop" and method == "DELETE":
            return httpx.Response(204)
        if path == "/v1/secrets" and method == "GET":
            return httpx.Response(200, json={"names": ["GITHUB_TOKEN"]})
        if path == "/v1/secrets/github/token" and method == "PUT":
            assert json.loads(request.content) == {"value": "s3cret"}
            return httpx.Response(200, json={"name": "github/token"})
        if path == "/v1/secrets/github/token" and method == "DELETE":
            return httpx.Response(204)
        if path == "/v1/catalog/tool_set/check":
            return httpx.Response(200, json={"ok": False, "digest": "sha256:x",
                                             "checks": [{"check": "c", "ok": False, "message": "m", "code": "X"}]})
        return httpx.Response(500)

    seen: list[httpx.Request] = []
    async with _api(db_session, owner, settings, monkeypatch, handler, seen) as api:
        assert (await api.get("/api/v1/runners")).json() == [{"name": "laptop", "online": True}]
        assert (await api.post("/api/v1/runners/laptop")).json() == {"name": "laptop", "token": "tok-once"}
        assert (await api.delete("/api/v1/runners/laptop")).status_code == 204
        before = len(seen)
        assert (await api.post("/api/v1/runners/Bad Name!")).status_code == 422
        assert len(seen) == before  # refused here, never sent upstream

        assert (await api.get("/api/v1/secrets")).json() == {"names": ["GITHUB_TOKEN"]}
        assert (await api.put("/api/v1/secrets/github/token", json={"value": "s3cret"})).status_code == 204
        assert (await api.delete("/api/v1/secrets/github/token")).status_code == 204
        assert (await api.put("/api/v1/secrets/a..b", json={"value": "x"})).status_code == 422
        assert (await api.put("/api/v1/secrets/ok", json={"value": ""})).status_code == 422

        check = (await api.post("/api/v1/catalog/tool_set/check", json={"name": "t"})).json()
        assert check["ok"] is False and check["checks"][0]["code"] == "X"
        assert (await api.post("/api/v1/catalog/nope/check", json={})).status_code == 404


@pytest.mark.asyncio
async def test_the_fleet_is_for_admins(db_session, make_user, settings, monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"workers": [], "path": request.url.path})

    member = await make_user(email="m@example.com")
    admin = await make_user(email="a@example.com", role="admin")
    seen: list[httpx.Request] = []
    async with _api(db_session, member, settings, monkeypatch, handler, seen) as api:
        assert (await api.get("/api/v1/ops/workers")).status_code == 403
    async with _api(db_session, admin, settings, monkeypatch, handler, seen) as api:
        assert (await api.get("/api/v1/ops/workers")).json()["path"] == "/v1/ops/workers"
        assert (await api.get("/api/v1/ops/rollout")).json()["path"] == "/v1/ops/rollout"


@pytest.mark.asyncio
async def test_supervisor_errors_keep_their_status(db_session, owner, settings, monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(422, json={"detail": "the window is at most 92 days"})

    seen: list[httpx.Request] = []
    async with _api(db_session, owner, settings, monkeypatch, handler, seen) as api:
        r = await api.get("/api/v1/usage", params={"since_ms": 0, "until_ms": 10**12})
        assert r.status_code == 422
