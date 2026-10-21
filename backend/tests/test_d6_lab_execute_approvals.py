"""Lab execute + BFF approvals routes (D6 P1 product gaps)."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import httpx
import pytest

from prime_agent.config import Settings
from prime_agent.db.models.session_link import SessionLink
from prime_agent.main import create_app
from prime_agent.services.ai_client import AiClient
from prime_agent.web.dependencies import get_current_user, get_db


@pytest.fixture
async def owner_user(make_user):
    return await make_user(email="lab-owner@example.com", name="Lab Owner")


@pytest.fixture
async def client(db_session, owner_user):
    app = create_app()

    async def override_db():
        yield db_session

    async def override_current_user():
        return owner_user

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = override_current_user
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as api:
        yield api


@pytest.mark.asyncio
async def test_bff_approvals_list_and_respond(db_session, owner_user, monkeypatch):
    app = create_app()
    link = SessionLink(
        id=str(uuid4()),
        user_id=owner_user.id,
        harness_id="h1",
        ai_session_id="ai-sess-1",
        tenant_id=owner_user.id,
        harness_version="githi-v1",
        runtime="githi",
        composition_id="githi-v1",
        composition_digest="sha256:abc",
        status="awaiting_approval",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    db_session.add(link)
    await db_session.commit()

    async def override_db():
        yield db_session

    async def override_current_user():
        return owner_user

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = override_current_user

    async def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/approvals") and request.method == "GET":
            return httpx.Response(
                200,
                json=[
                    {
                        "approval_id": "appr-1",
                        "session_id": "ai-sess-1",
                        "prompt": "Allow write?",
                        "options": ["allow", "deny"],
                        "status": "pending",
                        "choice": None,
                        "timeout_s": 300,
                    }
                ],
            )
        if path.endswith("/approvals/appr-1") is False and "/v1/approvals/appr-1" in path:
            return httpx.Response(
                200,
                json={
                    "approval_id": "appr-1",
                    "session_id": "ai-sess-1",
                    "prompt": "Allow write?",
                    "options": ["allow", "deny"],
                    "status": "resolved",
                    "choice": "allow",
                    "timeout_s": 300,
                },
            )
        if request.method == "POST" and path.rstrip("/").endswith("/approvals/appr-1"):
            return httpx.Response(
                200,
                json={
                    "approval_id": "appr-1",
                    "session_id": "ai-sess-1",
                    "prompt": "Allow write?",
                    "options": ["allow", "deny"],
                    "status": "resolved",
                    "choice": "allow",
                    "timeout_s": 300,
                },
            )
        return httpx.Response(404, json={"detail": path})

    # Its own settings: never whatever supervisor URL the developer's environment has.
    settings = Settings(
        environment="development",
        ai_supervisor_base_url="http://supervisor.test",
        ai_service_jwt_secret="ai-service-jwt-secret-at-least-32-bytes!!",
    )

    def make_client(s=None):
        return AiClient(settings, transport=httpx.MockTransport(handler))

    monkeypatch.setattr(
        "prime_agent.services.session_service.AiClient.from_settings",
        make_client,
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as api:
        listed = await api.get(f"/api/v1/sessions/{link.id}/approvals")
        assert listed.status_code == 200, listed.text
        rows = listed.json()
        assert len(rows) == 1
        assert rows[0]["approval_id"] == "appr-1"
        assert rows[0]["session_id"] == link.id

        responded = await api.post(
            f"/api/v1/sessions/{link.id}/approvals/appr-1",
            json={"choice": "allow"},
        )
        assert responded.status_code == 200, responded.text
        assert responded.json()["status"] == "resolved"
        assert responded.json()["choice"] == "allow"
