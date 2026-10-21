"""Sharing a workspace scope: people are invited by email and listed with it."""

from __future__ import annotations

import json

import httpx

from prime_agent.config import Settings
from prime_agent.main import create_app
from prime_agent.services.ai_client import AiClient
from prime_agent.services.workspace_service import WorkspaceService
from prime_agent.web.dependencies import get_current_user, get_db

SETTINGS = Settings(
    environment="development",
    ai_supervisor_base_url="http://supervisor.test",
    ai_service_jwt_secret="ai-service-jwt-secret-at-least-32-bytes!!",
)


def _api(db, user, handler, monkeypatch) -> httpx.AsyncClient:
    client = AiClient(SETTINGS, transport=httpx.MockTransport(handler))
    monkeypatch.setattr("prime_agent.web.routes.workspace._service", lambda: WorkspaceService(SETTINGS, client=client))
    app = create_app()

    async def override_db():
        yield db

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = lambda: user
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def test_invite_by_email_sends_the_users_principal_and_lists_their_email(db_session, make_user, monkeypatch):
    owner = await make_user(email="owner@example.com", name="Owner")
    bob = await make_user(email="Bob@Example.com", name="Bob")
    sent: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and request.url.path.endswith("/members"):
            body = json.loads(request.content)
            sent.append(body)
            return httpx.Response(201, json={"principal_id": body["principal_id"], "grants": ["workspace:read"]})
        if request.method == "GET" and request.url.path.endswith("/members"):
            return httpx.Response(200, json=[
                {"principal_id": owner.id, "grants": ["workspace:write"]},
                {"principal_id": bob.id, "grants": ["workspace:read"]},
                {"principal_id": "svc-runner", "grants": ["sandbox:exec"]},
            ])
        return httpx.Response(404)

    async with _api(db_session, owner, handler, monkeypatch) as api:
        invited = await api.post("/api/v1/workspace/scopes/s1/members", json={"email": " bob@example.com "})
        listed = await api.get("/api/v1/workspace/scopes/s1/members")
    assert invited.status_code == 201, invited.text
    assert sent[0]["principal_id"] == bob.id  # case and spaces ignored
    assert invited.json()["email"] == "Bob@Example.com"
    emails = {m["principal_id"]: m["email"] for m in listed.json()}
    assert emails == {owner.id: "owner@example.com", bob.id: "Bob@Example.com", "svc-runner": None}


async def test_an_unknown_email_is_404_and_nothing_is_sent(db_session, make_user, monkeypatch):
    owner = await make_user(email="owner2@example.com", name="Owner")
    calls: list[httpx.Request] = []
    async with _api(db_session, owner, lambda r: calls.append(r) or httpx.Response(500), monkeypatch) as api:
        r = await api.post("/api/v1/workspace/scopes/s1/members", json={"email": "nobody@example.com"})
        both = await api.post("/api/v1/workspace/scopes/s1/members", json={"email": "a@example.com", "principal_id": "p"})
    assert r.status_code == 404 and calls == []
    assert both.status_code == 422
