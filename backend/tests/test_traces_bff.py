"""Run traces through the BFF: the supervisor's runs named by the user's sessions, agents and
version numbers, and nothing of anyone else's."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import httpx
import pytest

from prime_agent.config import Settings, get_settings
from prime_agent.db.models.harness_release import HarnessRelease
from prime_agent.db.models.session_link import SessionLink
from prime_agent.main import create_app
from prime_agent.services.ai_client import AiClient
from prime_agent.services.harness_service import HarnessService
from prime_agent.services.trace_service import TraceService
from prime_agent.web.dependencies import get_current_user, get_db

V1 = "sha256:" + "1" * 64
V2 = "sha256:" + "2" * 64


@pytest.fixture
def settings(monkeypatch):
    s = Settings(
        environment="development",
        secret_key="user-secret-key-also-at-least-32-bytes!!",
        ai_supervisor_base_url="http://supervisor.test",
        ai_service_jwt_secret="ai-service-jwt-secret-at-least-32-bytes!!",
    )
    get_settings.cache_clear()
    for target in (
        "prime_agent.config.get_settings",
        "prime_agent.config.settings.get_settings",
        "prime_agent.services.ai_client.get_settings",
        "prime_agent.security.service_jwt.get_settings",
        "prime_agent.web.routes.traces.get_settings",
    ):
        monkeypatch.setattr(target, lambda: s)
    yield s
    get_settings.cache_clear()


@pytest.fixture
async def owner(make_user):
    return await make_user(email="traces@example.com", name="Traces")


@pytest.fixture
async def agent(db_session, owner):
    harness = await HarnessService.acreate(
        {"name": "reviewer", "model": "gateway/echo", "mode": "sticky", "user_id": owner.id, "created_by": owner.id},
        db_session,
    )
    harness.bind_status = "bound"
    harness.composition_id = "h-reviewer"
    harness.composition_digest = V2
    for version, digest in ((1, V1), (2, V2)):
        db_session.add(HarnessRelease(harness_id=harness.id, version=version, manifest={}, composition_id="h-reviewer",
                                      composition_digest=digest, harness_version=f"h-reviewer-r{version}",
                                      runtime="githi", created_by=owner.id))
    await db_session.flush()
    return harness


async def _link(db_session, user, agent, ai_session_id: str, digest: str) -> SessionLink:
    link = SessionLink(id=str(uuid4()), user_id=user.id, harness_id=agent.id, ai_session_id=ai_session_id,
                       tenant_id=user.id, harness_version="h-reviewer-r1", runtime="githi", composition_id="h-reviewer",
                       composition_digest=digest, status="active", title="Fix the parser",
                       created_at=datetime.now(UTC), updated_at=datetime.now(UTC))
    db_session.add(link)
    await db_session.flush()
    return link


def _run(ai_session_id: str, run_id: str, digest: str, **extra) -> dict:
    return {"session_id": ai_session_id, "run_id": run_id, "trace_id": "t" * 32, "status": "ok", "reason": None,
            "started_ms": 1000, "ended_ms": 2000, "duration_ms": 1000, "harness_version": "h-reviewer-r1",
            "composition_id": "h-reviewer", "composition_digest": digest, "input": "fix it", "output": "fixed",
            "cost": 0.03, "input_tokens": 100, "output_tokens": 10, "steps": 2, "tool_calls": 1, "tool_errors": 0,
            "denials": 0, "errors": 0, "time": {"model_ms": 600, "tool_ms": 200, "human_ms": 0, "other_ms": 200},
            "tools": {"bash": 1}, "models": ["m1"], **extra}


def _api(db_session, user, settings, monkeypatch, handler) -> httpx.AsyncClient:
    monkeypatch.setattr(
        "prime_agent.web.routes.traces.TraceService.from_settings",
        lambda s=None: TraceService(settings, client=AiClient(settings, transport=httpx.MockTransport(handler))),
    )
    app = create_app()

    async def override_db():
        yield db_session

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = lambda: user
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


@pytest.mark.asyncio
async def test_runs_are_named_by_the_users_session_agent_and_version(db_session, owner, agent, settings, monkeypatch):
    old = await _link(db_session, owner, agent, "ai-old", V1)
    new = await _link(db_session, owner, agent, "ai-new", V2)
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        assert request.url.path == "/v1/traces"
        return httpx.Response(200, json={"items": [
            _run("ai-new", "r2", V2),
            _run("ai-old", "r1", V1),
            _run("ai-someone-elses", "r9", V2),  # no link of this user's: left out
        ], "next_before_ms": 1000})

    async with _api(db_session, owner, settings, monkeypatch, handler) as api:
        r = await api.get("/api/v1/traces", params={"status": ["ok", "error"], "errors": "true", "q": "fix"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert [(i["session_id"], i["version"]) for i in body["items"]] == [(new.id, 2), (old.id, 1)]
    assert body["items"][0]["agent_name"] == "reviewer" and body["items"][0]["session_title"] == "Fix the parser"
    assert body["next_before_ms"] == 1000
    sent = seen[0].url.params
    assert sent.get_list("status") == ["ok", "error"] and sent["errors"] == "true" and sent["q"] == "fix"


@pytest.mark.asyncio
async def test_filtering_by_agent_asks_for_its_composition_and_a_stranger_agent_gets_nothing(
    db_session, owner, agent, settings, monkeypatch, make_user
):
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"items": [], "next_before_ms": None})

    async with _api(db_session, owner, settings, monkeypatch, handler) as api:
        assert (await api.get("/api/v1/traces", params={"agent_id": agent.id})).status_code == 200
    assert seen[0].url.params["composition_id"] == "h-reviewer"

    stranger = await make_user(email="stranger@example.com", name="S")
    seen.clear()
    async with _api(db_session, stranger, settings, monkeypatch, handler) as api:
        r = await api.get("/api/v1/traces", params={"agent_id": agent.id})
    assert r.json() == {"items": [], "next_before_ms": None} and not seen


@pytest.mark.asyncio
async def test_a_run_tree_and_its_spans_are_only_the_owners(db_session, owner, agent, settings, monkeypatch, make_user):
    link = await _link(db_session, owner, agent, "ai-s", V1)
    span = {"id": "abc", "parent_id": None, "kind": "tool", "name": "bash", "detail": {"args": {"command": "make"}}}
    approval = {"id": "ap", "parent_id": "abc", "kind": "approval", "name": "approval bash",
                "attrs": {"responded_by": owner.id, "decision": "allow"}}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/v1/sessions/ai-s/runs/r1/trace":
            return httpx.Response(200, json={"session_id": "ai-s", "run_id": "r1", "trace_id": "t" * 32,
                                             "composition_digest": V1, "rollup": {"status": "ok"}, "spans": [span, approval]})
        if path == "/v1/sessions/ai-s/runs/r1/trace/spans/abc":
            return httpx.Response(200, json=span)
        if path == "/v1/sessions/ai-s/runs/r1/trace/events":
            return httpx.Response(200, json={"session_id": "ai-s", "run_id": "r1", "events": [{"seq": 0}]})
        if path == "/v1/sessions/ai-s/traces":
            return httpx.Response(200, json={"session_id": "ai-s", "status": "active", "items": [_run("ai-s", "r1", V1)]})
        return httpx.Response(404, json={"detail": "run not found"})

    base = f"/api/v1/traces/sessions/{link.id}"
    async with _api(db_session, owner, settings, monkeypatch, handler) as api:
        tree = await api.get(f"{base}/runs/r1")
        assert tree.status_code == 200, tree.text
        assert tree.json()["version"] == 1 and tree.json()["agent_name"] == "reviewer"
        assert tree.json()["session_id"] == link.id and tree.json()["spans"][0]["kind"] == "tool"
        assert tree.json()["spans"][1]["attrs"]["responded_by"] == "you"  # not a principal id
        assert (await api.get(f"{base}/runs/r1/spans/abc")).json()["detail"]["args"] == {"command": "make"}
        events = (await api.get(f"{base}/runs/r1/events")).json()
        assert events == {"session_id": link.id, "run_id": "r1", "events": [{"seq": 0}]}
        replay = (await api.get(base)).json()
        assert replay["agent_name"] == "reviewer" and [i["run_id"] for i in replay["items"]] == ["r1"]
        assert (await api.get(f"{base}/runs/nope")).status_code == 404

    stranger = await make_user(email="stranger2@example.com", name="S")
    async with _api(db_session, stranger, settings, monkeypatch, handler) as api:
        assert (await api.get(f"{base}/runs/r1")).status_code == 404
        assert (await api.get(f"{base}/runs/r1/spans/abc")).status_code == 404
        assert (await api.get(base)).status_code == 404
        assert (await api.get(f"{base}/runs/r1/events")).status_code == 404


@pytest.mark.asyncio
async def test_an_unknown_status_is_refused(db_session, owner, settings, monkeypatch):
    async with _api(db_session, owner, settings, monkeypatch, lambda r: httpx.Response(500)) as api:
        assert (await api.get("/api/v1/traces", params={"status": "sideways"})).status_code == 422
