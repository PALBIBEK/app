"""HarnessService business-rule tests."""

import pytest
from fastapi import HTTPException

from prime_agent.db.models.harness import Harness
from prime_agent.db.models.tool import Tool
from prime_agent.services.harness_service import HarnessService


def _harness_payload(user_id: str, **overrides) -> dict:
    data = {
        "user_id": user_id,
        "name": "test-harness",
        "model": "gpt-4o",
        "mode": "agent",
        "tools": [],
        "memory": [],
    }
    data.update(overrides)
    return data


@pytest.mark.asyncio
async def test_acreate_persists_harness_for_user(db_session, user):
    harness = await HarnessService.acreate(_harness_payload(user.id), db_session)
    assert harness.id is not None
    assert harness.user_id == user.id
    assert harness.name == "test-harness"


@pytest.mark.asyncio
async def test_pre_save_rejects_duplicate_name_for_same_user(db_session, user):
    await HarnessService.acreate(_harness_payload(user.id), db_session)
    with pytest.raises(HTTPException) as exc_info:
        await HarnessService.acreate(_harness_payload(user.id), db_session)
    assert exc_info.value.status_code == 400


@pytest.mark.asyncio
async def test_pre_save_allows_same_name_for_different_user(db_session, user, make_user):
    other = await make_user(email="other@example.com", name="Other")
    await HarnessService.acreate(_harness_payload(user.id), db_session)
    other_harness = await HarnessService.acreate(_harness_payload(other.id), db_session)
    assert other_harness.user_id == other.id


@pytest.mark.asyncio
async def test_any_model_label_is_accepted_the_gateway_checks_it_at_publish(db_session, user):
    harness = await HarnessService.acreate(_harness_payload(user.id, model="gateway/echo"), db_session)
    assert harness.model == "gateway/echo"


@pytest.mark.asyncio
async def test_pre_save_rejects_invalid_mode(db_session, user):
    with pytest.raises(HTTPException) as exc_info:
        await HarnessService.acreate(
            _harness_payload(user.id, mode="telepathy"),
            db_session,
        )
    assert exc_info.value.status_code == 400


@pytest.mark.asyncio
async def test_pre_save_rejects_temperature_out_of_range(db_session, user):
    with pytest.raises(HTTPException) as exc_info:
        await HarnessService.acreate(
            _harness_payload(user.id, temperature=2.5),
            db_session,
        )
    assert exc_info.value.status_code == 400


@pytest.mark.asyncio
async def test_pre_save_rejects_non_positive_max_tokens(db_session, user):
    with pytest.raises(HTTPException) as exc_info:
        await HarnessService.acreate(
            _harness_payload(user.id, max_tokens=0),
            db_session,
        )
    assert exc_info.value.status_code == 400


@pytest.mark.asyncio
async def test_pre_save_rejects_unowned_tool_ids_with_404(db_session, user, make_user):
    other = await make_user(email="other@example.com")
    foreign_tool = Tool(
        user_id=other.id,
        name="Foreign",
        description="Hidden",
        category="search",
        status="active",
        parameters=[],
        auth_required=False,
    )
    db_session.add(foreign_tool)
    await db_session.commit()

    with pytest.raises(HTTPException) as exc_info:
        await HarnessService.acreate(
            _harness_payload(user.id, tools=[foreign_tool.id]),
            db_session,
        )
    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_pre_save_rejects_memory_with_422(db_session, user):
    with pytest.raises(HTTPException) as exc_info:
        await HarnessService.acreate(
            _harness_payload(user.id, memory=["memory-1"]),
            db_session,
        )
    assert exc_info.value.status_code == 422


@pytest.mark.asyncio
async def test_get_by_name_returns_none_or_404_when_error(db_session):
    assert await HarnessService.get_by_name("missing", db_session) is None
    with pytest.raises(HTTPException) as exc_info:
        await HarnessService.get_by_name("missing", db_session, error=True)
    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_get_by_model_filters(db_session, user):
    chat = await HarnessService.acreate(
        _harness_payload(user.id, name="chat-h", model="gpt-4o", mode="chat"),
        db_session,
    )
    await HarnessService.acreate(
        _harness_payload(user.id, name="agent-h", model="claude-sonnet-4", mode="agent"),
        db_session,
    )
    by_model = await HarnessService.get_by_model("gpt-4o", db_session)
    assert [item.id for item in by_model] == [chat.id]


@pytest.mark.asyncio
async def test_get_by_mode_filters(db_session, user):
    await HarnessService.acreate(
        _harness_payload(user.id, name="chat-h", model="gpt-4o", mode="chat"),
        db_session,
    )
    await HarnessService.acreate(
        _harness_payload(user.id, name="agent-h", model="claude-sonnet-4", mode="agent"),
        db_session,
    )
    by_mode = await HarnessService.get_by_mode("agent", db_session)
    assert len(by_mode) == 1
    # Legacy "agent" normalizes to canonical "stateless"
    assert by_mode[0].mode == "stateless"


@pytest.mark.asyncio
async def test_get_owned_raises_404_for_foreign_harness(db_session, user, make_user):
    other = await make_user(email="other@example.com")
    harness = Harness(
        user_id=other.id,
        name="Foreign",
        model="gpt-4o",
        mode="chat",
        tools=[],
        memory=[],
    )
    db_session.add(harness)
    await db_session.commit()

    with pytest.raises(HTTPException) as exc_info:
        await HarnessService.get_owned(harness.id, user.id, db_session)
    assert exc_info.value.status_code == 404
