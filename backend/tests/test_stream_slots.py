"""A person holds at most so many live event streams at once; each is given back as it ends.

Nothing limited how many one person could open, and each holds a connection to the
supervisor that polls the session's journal four times a second.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from uuid import uuid4

import httpx
import pytest
from fastapi import HTTPException

from prime_agent.config import Settings
from prime_agent.db.models.session_link import SessionLink
from prime_agent.main import create_app
from prime_agent.services.ai_client import AiClient
from prime_agent.services.harness_service import HarnessService
from prime_agent.services.session_service import SessionService
from prime_agent.web.dependencies import get_current_user, get_db
from prime_agent.web.routes import sessions as session_routes
from prime_agent.web.stream_slots import StreamSlots

pytestmark = pytest.mark.asyncio


async def _chunks(*parts: bytes) -> AsyncIterator[bytes]:
    for part in parts:
        yield part


async def test_a_person_gets_slots_up_to_the_limit_and_others_are_not_held_up() -> None:
    slots = StreamSlots(limit=2)
    first, _second = slots.claim("alice"), slots.claim("alice")
    with pytest.raises(HTTPException) as refused:
        slots.claim("alice")
    assert refused.value.status_code == 429
    slots.claim("bob")  # someone else is not held up
    first.release()
    first.release()  # once only
    assert slots.open_for("alice") == 1
    slots.claim("alice")


async def test_a_slot_is_given_back_when_its_stream_ends_or_is_dropped() -> None:
    slots = StreamSlots(limit=5)
    served = [chunk async for chunk in slots.claim("alice").serve(_chunks(b"a", b"b"))]
    assert served == [b"a", b"b"] and slots.open_for("alice") == 0
    dropped = slots.claim("alice").serve(_chunks(b"a", b"b", b"c"))
    assert await anext(dropped) == b"a"
    await dropped.aclose()  # the browser went away mid-stream
    assert slots.open_for("alice") == 0


@pytest.fixture
async def api(db_session, make_user, monkeypatch: pytest.MonkeyPatch):  # noqa: ANN001, ANN201
    owner = await make_user(email="streamer@example.com")
    harness = await HarnessService.acreate(
        {"name": "h", "model": "m", "mode": "agent", "tools": [], "memory": [], "user_id": owner.id, "created_by": owner.id},
        db_session,
    )
    link = SessionLink(
        id=str(uuid4()), user_id=owner.id, harness_id=harness.id, ai_session_id="ai-1", tenant_id=owner.id,
        harness_version="githi-v1", runtime="githi", status="active",
        created_at=datetime.now(UTC), updated_at=datetime.now(UTC),
    )
    db_session.add(link)
    await db_session.commit()
    settings = Settings(
        environment="development",
        secret_key="user-secret-key-also-at-least-32-bytes!!",
        ai_supervisor_base_url="http://supervisor.test",
        ai_service_jwt_secret="ai-service-jwt-secret-at-least-32-bytes!!",
    )

    def supervisor(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/ai-1/events"):
            return httpx.Response(200, content=b"id: 0\ndata: {}\n\nevent: end\ndata: {}\n\n")
        return httpx.Response(404)

    monkeypatch.setattr(
        "prime_agent.web.routes.sessions.SessionService.from_settings",
        lambda _s=None: SessionService(settings, client=AiClient(settings, transport=httpx.MockTransport(supervisor))),
    )
    slots = StreamSlots(limit=1)
    monkeypatch.setattr(session_routes, "STREAMS", slots)
    app = create_app()

    async def override_db():  # noqa: ANN202
        yield db_session

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = lambda: owner
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as client:
        yield client, slots, owner.id, link.id


async def test_a_finished_stream_gives_its_slot_back(api) -> None:  # noqa: ANN001
    client, slots, owner_id, session_id = api
    for _ in range(3):  # one at a time, again and again
        streamed = await client.get(f"/api/v1/sessions/{session_id}/events")
        assert streamed.status_code == 200 and b"event: end" in streamed.content
    assert slots.open_for(owner_id) == 0


async def test_past_the_limit_the_next_stream_is_refused(api) -> None:  # noqa: ANN001
    client, slots, owner_id, session_id = api
    held = slots.claim(owner_id)  # another tab holds the one slot
    refused = await client.get(f"/api/v1/sessions/{session_id}/events")
    assert refused.status_code == 429
    assert refused.json()["detail"]["error_code"] == "TOO_MANY_STREAMS"
    held.release()
    assert (await client.get(f"/api/v1/sessions/{session_id}/events")).status_code == 200


async def test_a_stream_that_could_not_open_holds_no_slot(api) -> None:  # noqa: ANN001
    client, slots, owner_id, _session_id = api
    assert (await client.get("/api/v1/sessions/not-mine/events")).status_code == 404
    assert slots.open_for(owner_id) == 0
