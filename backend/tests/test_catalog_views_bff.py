"""What a session runs, and how much each agent version is used, through the BFF.

The supervisor speaks composition digests; these routes name them the way a
person does: the agent's version number.
"""

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
from prime_agent.services.session_service import SessionService
from prime_agent.web.dependencies import get_current_user, get_db

V1 = "sha256:" + "1" * 63 + "a"
V2 = "sha256:" + "2" * 63 + "b"
LEGACY = "sha256:" + "3" * 63 + "c"


@pytest.fixture
def session_settings(monkeypatch):
    settings = Settings(
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
    ):
        monkeypatch.setattr(target, lambda: settings)
    yield settings
    get_settings.cache_clear()


@pytest.fixture
async def owner(make_user):
    return await make_user(email="views@example.com", name="Views")


@pytest.fixture
async def agent(db_session, owner):
    harness = await HarnessService.acreate(
        {"name": "reviewer", "model": "gateway/echo", "mode": "sticky", "user_id": owner.id, "created_by": owner.id},
        db_session,
    )
    harness.bind_status = "bound"
    harness.composition_id = "h-reviewer"
    harness.composition_digest = V2
    harness.harness_version = "h-reviewer-r2"
    harness.runtime = "githi"
    for version, digest in ((1, V1), (2, V2)):
        db_session.add(
            HarnessRelease(
                harness_id=harness.id,
                version=version,
                manifest={},
                composition_id="h-reviewer",
                composition_digest=digest,
                harness_version=f"h-reviewer-r{version}",
                runtime="githi",
                created_by=owner.id,
            )
        )
    await db_session.flush()
    return harness


def _api(db_session, user, settings, monkeypatch, handler) -> httpx.AsyncClient:
    def client(s=None) -> AiClient:
        return AiClient(s or settings, transport=httpx.MockTransport(handler))

    monkeypatch.setattr("prime_agent.web.routes.sessions.SessionService.from_settings", lambda s=None: SessionService(settings, client=client()))
    monkeypatch.setattr("prime_agent.web.routes.harnesses.AiClient.from_settings", client)
    monkeypatch.setattr("prime_agent.web.routes.harnesses.get_settings", lambda: settings)
    app = create_app()

    async def override_db():
        yield db_session

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = lambda: user
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


@pytest.mark.asyncio
async def test_a_session_shows_its_parts_and_the_agent_version_it_runs(
    db_session, owner, agent, session_settings, monkeypatch, make_user
):
    link = SessionLink(
        id=str(uuid4()),
        user_id=owner.id,
        harness_id=agent.id,
        ai_session_id="ai-pins",
        tenant_id=owner.id,
        harness_version="h-reviewer-r1",
        runtime="githi",
        composition_id="h-reviewer",
        composition_digest=V1,
        status="active",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    db_session.add(link)
    await db_session.flush()

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/sessions/ai-pins/pins"
        return httpx.Response(200, json={
            "composition_id": "h-reviewer", "composition_digest": V1, "composition_semver": "1.1.0",
            "composition_status": "deprecated", "harness_version": "h-reviewer-r1", "model_route": "echo",
            "mcp_configured": False, "acp_configured": False,
            "parts": [{"kind": "tool_set", "role": "tools", "artifact_id": "h-reviewer-tools", "digest": V1,
                       "semver": "1.1.0", "status": "offered", "settings": {"tools": [{"name": "bash", "tool_class": "non_atomic"}]}}],
        })

    async with _api(db_session, owner, session_settings, monkeypatch, handler) as api:
        r = await api.get(f"/api/v1/sessions/{link.id}/pins")
        assert r.status_code == 200, r.text
        assert r.json()["release_version"] == 1  # the digest, named as the agent's version
        assert r.json()["composition_status"] == "deprecated"
        assert r.json()["parts"][0]["settings"]["tools"][0]["name"] == "bash"

    stranger = await make_user(email="stranger@example.com", name="S")
    async with _api(db_session, stranger, session_settings, monkeypatch, handler) as api:
        assert (await api.get(f"/api/v1/sessions/{link.id}/pins")).status_code == 404


@pytest.mark.asyncio
async def test_usage_numbers_each_pinned_version(db_session, owner, agent, session_settings, monkeypatch):
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={
            "artifact_kind": "composition", "artifact_id": "h-reviewer", "artifact_digest": None,
            "session_count": 5, "active_sessions": 2, "unique_principals": 1,
            "first_used_at": "2026-10-01T00:00:00+00:00", "last_used_at": "2026-10-26T00:00:00+00:00",
            "versions": [{"digest": V2, "sessions": 3}, {"digest": V1, "sessions": 1}, {"digest": LEGACY, "sessions": 1}],
        })

    async with _api(db_session, owner, session_settings, monkeypatch, handler) as api:
        r = await api.get(f"/api/v1/harnesses/{agent.id}/usage")
    assert r.status_code == 200, r.text
    assert seen[0].url.path == "/v1/catalog/composition/h-reviewer/usage"
    body = r.json()
    assert (body["session_count"], body["active_sessions"]) == (5, 2)
    assert [(v["version"], v["sessions"]) for v in body["versions"]] == [(2, 3), (1, 1), (None, 1)]


@pytest.mark.asyncio
async def test_an_unpublished_agent_has_no_usage_and_asks_nothing(db_session, owner, session_settings, monkeypatch):
    draft = await HarnessService.acreate(
        {"name": "draft", "model": "gateway/echo", "mode": "sticky", "user_id": owner.id, "created_by": owner.id},
        db_session,
    )

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("an unpublished agent needs no supervisor call")

    async with _api(db_session, owner, session_settings, monkeypatch, handler) as api:
        r = await api.get(f"/api/v1/harnesses/{draft.id}/usage")
    assert r.status_code == 200 and r.json() == {
        "session_count": 0, "active_sessions": 0, "first_used_at": None, "last_used_at": None, "versions": []
    }
