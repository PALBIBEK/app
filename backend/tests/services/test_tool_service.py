"""ToolService business-rule tests."""

import pytest
from fastapi import HTTPException

from prime_agent.db.models.tool import Tool
from prime_agent.services.tool_service import ToolService


def _tool_payload(user_id: str, **overrides) -> dict:
    data = {
        "user_id": user_id,
        "name": "search-tool",
        "description": "Search",
        "category": "search",
        "status": "active",
        "parameters": [],
        "auth_required": False,
    }
    data.update(overrides)
    return data


@pytest.mark.asyncio
async def test_acreate_persists_tool_for_user(db_session, user):
    tool = await ToolService.acreate(_tool_payload(user.id), db_session)
    assert tool.id is not None
    assert tool.user_id == user.id


@pytest.mark.asyncio
async def test_pre_save_rejects_duplicate_name_for_same_user(db_session, user):
    await ToolService.acreate(_tool_payload(user.id), db_session)
    with pytest.raises(HTTPException) as exc_info:
        await ToolService.acreate(_tool_payload(user.id), db_session)
    assert exc_info.value.status_code == 400


@pytest.mark.asyncio
async def test_pre_save_rejects_invalid_category(db_session, user):
    with pytest.raises(HTTPException) as exc_info:
        await ToolService.acreate(_tool_payload(user.id, category="espionage"), db_session)
    assert exc_info.value.status_code == 400


@pytest.mark.asyncio
async def test_pre_save_rejects_invalid_status(db_session, user):
    with pytest.raises(HTTPException) as exc_info:
        await ToolService.acreate(_tool_payload(user.id, status="broken"), db_session)
    assert exc_info.value.status_code == 400


@pytest.mark.asyncio
async def test_pre_save_rejects_non_positive_rate_limit(db_session, user):
    with pytest.raises(HTTPException) as exc_info:
        await ToolService.acreate(_tool_payload(user.id, rate_limit=0), db_session)
    assert exc_info.value.status_code == 400


@pytest.mark.asyncio
async def test_validate_endpoint_rejects_private_and_literal_ips():
    for endpoint in (
        "http://127.0.0.1/admin",
        "http://10.0.0.1/internal",
        "http://169.254.169.254/latest",
        "http://localhost/admin",
        "http://svc.internal/api",
    ):
        with pytest.raises(HTTPException) as exc_info:
            ToolService._validate_endpoint(endpoint)
        assert exc_info.value.status_code == 400
        assert exc_info.value.detail == "Endpoint must be a public HTTP(S) URL"


@pytest.mark.asyncio
async def test_validate_endpoint_accepts_public_https():
    ToolService._validate_endpoint("https://api.example.com/search")


@pytest.mark.asyncio
async def test_get_by_name_returns_none_or_404_when_error(db_session):
    assert await ToolService.get_by_name("missing", db_session) is None
    with pytest.raises(HTTPException) as exc_info:
        await ToolService.get_by_name("missing", db_session, error=True)
    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_get_by_category_filters(db_session, user):
    search = await ToolService.acreate(_tool_payload(user.id), db_session)
    await ToolService.acreate(
        _tool_payload(user.id, name="code-tool", category="code", status="inactive"),
        db_session,
    )
    by_category = await ToolService.get_by_category("search", db_session)
    assert [item.id for item in by_category] == [search.id]


@pytest.mark.asyncio
async def test_get_by_status_filters(db_session, user):
    await ToolService.acreate(_tool_payload(user.id), db_session)
    await ToolService.acreate(
        _tool_payload(user.id, name="code-tool", category="code", status="inactive"),
        db_session,
    )
    inactive = await ToolService.get_by_status("inactive", db_session)
    assert len(inactive) == 1
    assert inactive[0].status == "inactive"


@pytest.mark.asyncio
async def test_get_active_tools_returns_only_active(db_session, user):
    search = await ToolService.acreate(_tool_payload(user.id), db_session)
    await ToolService.acreate(
        _tool_payload(user.id, name="code-tool", category="code", status="inactive"),
        db_session,
    )
    active = await ToolService.get_active_tools(db_session)
    assert [item.id for item in active] == [search.id]


@pytest.mark.asyncio
async def test_get_owned_raises_404_for_foreign_tool(db_session, user, make_user):
    other = await make_user(email="other@example.com")
    tool = Tool(
        user_id=other.id,
        name="Foreign",
        description="Hidden",
        category="search",
        status="active",
        parameters=[],
        auth_required=False,
    )
    db_session.add(tool)
    await db_session.commit()

    with pytest.raises(HTTPException) as exc_info:
        await ToolService.get_owned(tool.id, user.id, db_session)
    assert exc_info.value.status_code == 404
