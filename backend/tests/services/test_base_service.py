"""BaseService CRUD contract tests using Tool as a concrete model."""

from uuid import uuid4

import pytest
from fastapi import HTTPException

from prime_agent.services.tool_service import ToolService


def _payload(user_id: str, name: str) -> dict:
    return {
        "user_id": user_id,
        "name": name,
        "description": "Base coverage",
        "category": "search",
        "status": "active",
        "parameters": [],
        "auth_required": False,
    }


@pytest.mark.asyncio
async def test_aget_one_raises_404_when_missing(db_session):
    with pytest.raises(HTTPException) as exc_info:
        await ToolService.aget_one(str(uuid4()), db_session)
    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_aget_one_returns_none_when_error_false(db_session):
    assert await ToolService.aget_one(str(uuid4()), db_session, error=False) is None


@pytest.mark.asyncio
async def test_aget_all_applies_filters_and_limit(db_session, user):
    first = await ToolService.acreate(_payload(user.id, "one"), db_session)
    await ToolService.acreate(_payload(user.id, "two"), db_session)
    filtered = await ToolService.aget_all(db_session, skip=0, limit=1, name="one")
    assert [item.id for item in filtered] == [first.id]
    limited = await ToolService.aget_all(db_session, skip=0, limit=1)
    assert len(limited) == 1


@pytest.mark.asyncio
async def test_count_respects_filters(db_session, user):
    await ToolService.acreate(_payload(user.id, "one"), db_session)
    await ToolService.acreate(_payload(user.id, "two"), db_session)
    assert await ToolService.count(db_session, name="one") == 1
    assert await ToolService.count(db_session) == 2


@pytest.mark.asyncio
async def test_aupdate_raises_404_for_missing_id(db_session):
    with pytest.raises(HTTPException) as exc_info:
        await ToolService.aupdate(str(uuid4()), {"description": "nope"}, db_session)
    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_adelete_raises_404_for_missing_id(db_session):
    with pytest.raises(HTTPException) as exc_info:
        await ToolService.adelete(str(uuid4()), db_session)
    assert exc_info.value.status_code == 404
