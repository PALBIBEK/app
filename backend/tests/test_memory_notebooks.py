"""Long-term memory routes: notebooks through the supervisor, as the signed-in user."""

from __future__ import annotations

import json

import httpx
import jwt
import pytest

from prime_agent.config import Settings
from prime_agent.main import create_app
from prime_agent.services.ai_client import AiClient
from prime_agent.services.harness_service import HarnessService
from prime_agent.services.memory_service import MemoryService
from prime_agent.web.dependencies import get_current_user, get_db
from tests.support.catalog import DIGEST, PIN

SECRET = "ai-service-jwt-secret-at-least-32-bytes!!"
AGENT_PIN = f"composition:githi-v1@{DIGEST}"
SETTINGS = Settings(
    environment="development",
    ai_supervisor_base_url="http://supervisor.test",
    ai_service_jwt_secret=SECRET,
)


@pytest.fixture
async def owner(make_user):
    return await make_user(email="mem@example.com", name="Mem User")


async def _harness(db, owner, *, bound: bool = True):
    data = {
        "name": f"mem-harness-{bound}",
        "model": "claude-sonnet-4",
        "mode": "sticky",
        "user_id": owner.id,
        "created_by": owner.id,
    }
    harness = await HarnessService.acreate(data, db)
    if bound:
        harness.bind_status = "bound"
        harness.composition_id = PIN["composition_id"]
        harness.composition_digest = DIGEST
        harness.harness_version = PIN["harness_version"]
        harness.runtime = PIN["runtime"]
        await db.flush()
    return harness


def _api(db, user, handler, monkeypatch) -> httpx.AsyncClient:
    client = AiClient(SETTINGS, transport=httpx.MockTransport(handler))
    monkeypatch.setattr("prime_agent.web.routes.memory._service", lambda: MemoryService(client))
    app = create_app()

    async def override_db():
        yield db

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = lambda: user
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def test_notebooks_of_a_harness_are_listed_by_its_agent_version(db_session, owner, monkeypatch):
    harness = await _harness(db_session, owner)
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=[{
            "id": "nb1", "agent_pin": AGENT_PIN, "name": "default", "label": None, "revision": 3,
            "created_at": 1, "archived": False, "facts": 2, "bytes": 40, "updated_at": 2,
        }])

    async with _api(db_session, owner, handler, monkeypatch) as api:
        r = await api.get("/api/v1/memory/notebooks", params={"harness_id": harness.id})
    assert r.status_code == 200, r.text
    assert r.json()[0]["facts"] == 2
    (call,) = seen
    assert call.url.path == "/v1/memory/notebooks"
    assert call.url.params["agent_pin"] == AGENT_PIN  # the pin a session of this harness runs as
    claims = jwt.decode(call.headers["authorization"].removeprefix("Bearer "), SECRET,
                        algorithms=["HS256"], audience="yal-supervisor", issuer="prime-agent-api")
    assert claims["type"] == "service"  # as the user, through a service token: never the browser's


async def test_an_unbound_harness_has_no_memory(db_session, owner, monkeypatch):
    harness = await _harness(db_session, owner, bound=False)
    async with _api(db_session, owner, lambda r: httpx.Response(500), monkeypatch) as api:
        r = await api.get("/api/v1/memory/notebooks", params={"harness_id": harness.id})
    assert r.status_code == 422
    assert r.json()["detail"]["error_code"] == "NOT_BOUND"


async def test_another_users_harness_is_not_found(db_session, owner, make_user, monkeypatch):
    harness = await _harness(db_session, owner)
    other = await make_user(email="other-mem@example.com", name="Other")
    calls: list[httpx.Request] = []
    async with _api(db_session, other, lambda r: calls.append(r) or httpx.Response(200, json=[]), monkeypatch) as api:
        r = await api.get("/api/v1/memory/notebooks", params={"harness_id": harness.id})
    assert r.status_code == 404
    assert calls == []  # refused before the supervisor is asked


async def test_forget_sends_the_ids_to_the_supervisor_and_its_refusals_come_back(db_session, owner, monkeypatch):
    seen: list[tuple[str, str, dict]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path, json.loads(request.content or b"{}")))
        if request.url.path.endswith("/nb-theirs/facts"):
            return httpx.Response(404, json={"detail": "notebook not found"})
        return httpx.Response(204)

    async with _api(db_session, owner, handler, monkeypatch) as api:
        ok = await api.post("/api/v1/memory/notebooks/nb1/forget", json={"ids": ["f1", "f2"]})
        theirs = await api.get("/api/v1/memory/notebooks/nb-theirs/facts")
    assert ok.status_code == 204
    assert seen[0] == ("DELETE", "/v1/memory/notebooks/nb1/facts", {"ids": ["f1", "f2"]})
    assert theirs.status_code == 404
    assert theirs.json()["detail"]["message"] == "notebook not found"


async def test_search_is_scoped_to_the_harness_agent_version(db_session, owner, monkeypatch):
    harness = await _harness(db_session, owner)
    bodies: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json=[{"notebook_id": "nb1", "fact": "User lives in Pune"}])

    async with _api(db_session, owner, handler, monkeypatch) as api:
        r = await api.post("/api/v1/memory/search", json={"harness_id": harness.id, "query": "Pune"})
    assert r.status_code == 200
    assert bodies == [{"agent_pin": AGENT_PIN, "query": "Pune", "limit": 20}]


async def test_memory_service_outage_is_503(db_session, owner, monkeypatch):
    harness = await _harness(db_session, owner)
    handler = lambda r: httpx.Response(503, json={"detail": "memory service unavailable"})  # noqa: E731
    async with _api(db_session, owner, handler, monkeypatch) as api:
        r = await api.get("/api/v1/memory/notebooks", params={"harness_id": harness.id})
    assert r.status_code == 503
    assert r.json()["detail"]["error_code"] == "MEMORY_UNAVAILABLE"


async def test_all_versions_lists_each_versions_notebooks_newest_first(db_session, owner, monkeypatch):
    from prime_agent.db.models.harness_release import HarnessRelease

    harness = await _harness(db_session, owner)
    old = "sha256:" + "0" * 64
    db_session.add_all([
        HarnessRelease(harness_id=harness.id, version=1, manifest={}, composition_id="githi-v1",
                       composition_digest=old, harness_version="h-r1", runtime="githi"),
        HarnessRelease(harness_id=harness.id, version=2, manifest={}, composition_id="githi-v1",
                       composition_digest=DIGEST, harness_version="h-r2", runtime="githi"),
    ])
    await db_session.flush()
    asked: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        pin = request.url.params["agent_pin"]
        asked.append(pin)
        return httpx.Response(200, json=[{
            "id": f"nb-{pin[-4:]}", "agent_pin": pin, "name": "default", "label": None, "revision": 1,
            "created_at": 1, "archived": False, "facts": 1, "bytes": 3, "updated_at": 1,
        }])

    async with _api(db_session, owner, handler, monkeypatch) as api:
        r = await api.get("/api/v1/memory/notebooks", params={"harness_id": harness.id, "all_versions": True})
    assert r.status_code == 200, r.text
    assert asked == [AGENT_PIN, f"composition:githi-v1@{old}"]  # the live version first
    assert [nb["version"] for nb in r.json()] == [2, 1]
