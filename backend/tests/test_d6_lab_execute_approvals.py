"""Lab execute + BFF approvals routes (D6 P1 product gaps)."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest

from prime_agent.db.models.session_link import SessionLink
from prime_agent.db.models.tool import Tool
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
async def test_lab_execute_ownership_and_ssrf(client, db_session, owner_user, make_user):
    other = await make_user(email="other-lab@example.com", name="Other")
    foreign = Tool(
        user_id=other.id,
        name="foreign-http",
        description="not yours",
        category="custom",
        status="active",
        parameters=[],
        endpoint="https://api.example.com/hook",
        auth_required=False,
    )
    db_session.add(foreign)
    await db_session.commit()

    foreign_exec = await client.post(
        f"/api/v1/tools/{foreign.id}/execute",
        json={"parameters": {"q": "x"}},
    )
    assert foreign_exec.status_code == 404

    create = await client.post(
        "/api/v1/tools",
        json={
            "name": "lab-http",
            "description": "Lab tool",
            "category": "custom",
            "endpoint": "https://api.example.com/search",
        },
    )
    assert create.status_code == 201
    tool_id = create.json()["id"]

    # SSRF re-check at call time — private IP endpoint must fail before request.
    tool = await db_session.get(Tool, tool_id)
    assert tool is not None
    tool.endpoint = "http://127.0.0.1/admin"
    await db_session.commit()

    blocked = await client.post(
        f"/api/v1/tools/{tool_id}/execute",
        json={"parameters": {"q": "x"}},
    )
    assert blocked.status_code == 400


@pytest.mark.asyncio
async def test_lab_execute_success_structured_result(
    client, db_session, owner_user, monkeypatch
):
    tool = Tool(
        user_id=owner_user.id,
        name="lab-ok",
        description="ok",
        category="custom",
        status="active",
        parameters=[],
        endpoint="https://api.example.com/run",
        auth_required=False,
    )
    db_session.add(tool)
    await db_session.commit()

    class _Resp:
        status_code = 200

        def json(self):
            return {"hits": [1]}

        text = '{"hits":[1]}'

    class _Client:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, url, json=None):
            assert url == "https://api.example.com/run"
            assert json == {"parameters": {"q": "d6"}}
            return _Resp()

    monkeypatch.setattr(
        "prime_agent.services.tool_service.httpx.AsyncClient",
        _Client,
    )
    # Bypass DNS resolve for example.com in lab path.
    monkeypatch.setattr(
        "prime_agent.services.tool_service.ToolService._validate_endpoint",
        staticmethod(lambda endpoint, *, resolve_dns=False: None),
    )

    resp = await client.post(
        f"/api/v1/tools/{tool.id}/execute",
        json={"parameters": {"q": "d6"}},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["execution_id"]
    assert body["result"]["status"] == "success"
    assert body["result"]["tool_id"] == tool.id
    assert body["result"]["result"] == {"hits": [1]}
    assert body["result"]["duration_ms"] >= 0


@pytest.mark.asyncio
async def test_bff_approvals_list_and_respond(db_session, owner_user, monkeypatch):
    app = create_app()
    link = SessionLink(
        id=str(uuid4()),
        user_id=owner_user.id,
        harness_id="h1",
        ai_session_id="ai-sess-1",
        tenant_id=owner_user.id,
        harness_version="coding-v1",
        runtime="harness",
        composition_id="coding-v1",
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

    def make_client(s=None):
        return AiClient(s, transport=httpx.MockTransport(handler))

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
