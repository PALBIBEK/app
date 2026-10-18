import httpx
import pytest

from prime_agent.db.models.harness import Harness
from prime_agent.db.models.tool import Tool
from prime_agent.main import create_app
from prime_agent.web.dependencies import get_current_user, get_db


@pytest.fixture
async def users(db_session, make_user):
    owner = await make_user(email="owner@example.com", name="Owner")
    other = await make_user(email="other@example.com", name="Other")
    return owner, other


@pytest.fixture
async def client(db_session, users):
    owner, _ = users
    app = create_app()

    async def override_db():
        yield db_session

    async def override_current_user():
        return owner

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = override_current_user
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as api:
        yield api


@pytest.mark.asyncio
async def test_harness_crud_is_paginated_and_owner_scoped(client, db_session, users):
    owner, other = users
    foreign_harness = Harness(
        user_id=other.id,
        name="Other User Harness",
        model="gpt-4o",
        mode="agent",
        tools=[],
        memory=[],
    )
    own_tool = Tool(
        user_id=owner.id,
        name="Owned Tool",
        description="Visible",
        category="search",
        status="active",
        parameters=[],
        auth_required=False,
    )
    foreign_tool = Tool(
        user_id=other.id,
        name="Foreign Tool",
        description="Hidden",
        category="search",
        status="active",
        parameters=[],
        auth_required=False,
    )
    db_session.add_all([foreign_harness, own_tool, foreign_tool])
    await db_session.commit()

    cross_owner_reference = await client.post(
        "/api/v1/harnesses",
        json={
            "name": "Invalid Harness",
            "model": "gpt-4o",
            "tools": [foreign_tool.id],
        },
    )
    assert cross_owner_reference.status_code == 404

    unavailable_memory = await client.post(
        "/api/v1/harnesses",
        json={
            "name": "Memory Harness",
            "model": "gpt-4o",
            "memory": ["unverified-memory-id"],
        },
    )
    assert unavailable_memory.status_code == 422

    create_response = await client.post(
        "/api/v1/harnesses",
        json={
            "name": "My Harness",
            "model": "gpt-4o",
            "mode": "agent",
            "tools": [own_tool.id],
        },
    )
    assert create_response.status_code == 201
    harness_id = create_response.json()["id"]

    list_response = await client.get("/api/v1/harnesses?page=1&page_size=10")
    assert list_response.status_code == 200
    assert list_response.json()["total"] == 1
    assert [item["id"] for item in list_response.json()["data"]] == [harness_id]

    foreign_response = await client.get(f"/api/v1/harnesses/{foreign_harness.id}")
    assert foreign_response.status_code == 404

    foreign_update = await client.put(
        f"/api/v1/harnesses/{foreign_harness.id}",
        json={"description": "stolen"},
    )
    assert foreign_update.status_code == 404

    foreign_delete = await client.delete(f"/api/v1/harnesses/{foreign_harness.id}")
    assert foreign_delete.status_code == 404

    update_response = await client.put(
        f"/api/v1/harnesses/{harness_id}",
        json={"description": "Updated"},
    )
    assert update_response.status_code == 200
    assert update_response.json()["description"] == "Updated"

    delete_response = await client.delete(f"/api/v1/harnesses/{harness_id}")
    assert delete_response.status_code == 204

    missing_response = await client.get(f"/api/v1/harnesses/{harness_id}")
    assert missing_response.status_code == 404
    assert owner.id != other.id
