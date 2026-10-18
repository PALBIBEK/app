import httpx
import pytest

from prime_agent.db.models.tool import Tool
from prime_agent.main import create_app
from prime_agent.web.dependencies import get_current_user, get_db


@pytest.fixture
async def users(make_user):
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
async def test_tool_crud_filtering_and_owner_scope(client, db_session, users):
    _, other = users
    foreign_tool = Tool(
        user_id=other.id,
        name="Foreign Search",
        description="Not visible",
        category="search",
        status="active",
        parameters=[],
        auth_required=False,
    )
    db_session.add(foreign_tool)
    await db_session.commit()

    for private_endpoint in (
        "http://127.0.0.1/admin",
        "http://169.254.169.254/latest/meta-data",
        "http://metadata.google.internal/",
        "http://2130706433/admin",
        "http://0177.0.0.1/admin",
        "http://[invalid",
    ):
        blocked_response = await client.post(
            "/api/v1/tools",
            json={
                "name": f"Blocked {private_endpoint}",
                "description": "Must not be stored",
                "category": "search",
                "endpoint": private_endpoint,
            },
        )
        assert blocked_response.status_code == 400

    create_response = await client.post(
        "/api/v1/tools",
        json={
            "name": "My Search",
            "description": "Search owned documents",
            "category": "search",
            "endpoint": "https://api.example.com/search",
        },
    )
    assert create_response.status_code == 201
    tool_id = create_response.json()["id"]

    list_response = await client.get("/api/v1/tools?category=search")
    assert list_response.status_code == 200
    assert list_response.json()["total"] == 1
    assert [item["id"] for item in list_response.json()["data"]] == [tool_id]

    invalid_filter = await client.get("/api/v1/tools?category=unknown")
    assert invalid_filter.status_code == 422

    foreign_response = await client.get(f"/api/v1/tools/{foreign_tool.id}")
    assert foreign_response.status_code == 404

    foreign_update = await client.put(
        f"/api/v1/tools/{foreign_tool.id}",
        json={"description": "stolen"},
    )
    assert foreign_update.status_code == 404

    foreign_delete = await client.delete(f"/api/v1/tools/{foreign_tool.id}")
    assert foreign_delete.status_code == 404

    update_response = await client.put(
        f"/api/v1/tools/{tool_id}",
        json={"description": "Updated"},
    )
    assert update_response.status_code == 200
    assert update_response.json()["description"] == "Updated"

    delete_response = await client.delete(f"/api/v1/tools/{tool_id}")
    assert delete_response.status_code == 204
