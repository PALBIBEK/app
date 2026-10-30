"""Deleting a harness ends and erases its sessions first.

It used to delete only the product's rows: the sessions' links went with the harness, and
the sessions themselves (journals, files, and any still running) stayed in the supervisor,
out of the user's reach.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import func, select

from prime_agent.config import Settings
from prime_agent.db.models.harness import Harness
from prime_agent.db.models.session_link import SessionLink
from prime_agent.main import create_app
from prime_agent.services.ai_client import AiClient
from prime_agent.services.harness_service import HarnessService
from prime_agent.web.dependencies import get_current_user, get_db

pytestmark = pytest.mark.asyncio
Handler = Callable[[httpx.Request], httpx.Response]


@pytest.fixture
async def owner(make_user):  # noqa: ANN001, ANN201
    return await make_user(email="owner@example.com")


@pytest.fixture
async def harness(db_session, owner):  # noqa: ANN001, ANN201
    made = await HarnessService.acreate(
        {"name": "doomed", "model": "m", "mode": "agent", "tools": [], "memory": [],
         "user_id": owner.id, "created_by": owner.id},
        db_session,
    )
    for n in range(3):
        db_session.add(SessionLink(
            id=str(uuid4()), user_id=owner.id, harness_id=made.id, ai_session_id=f"ai-{n}",
            tenant_id=owner.id, harness_version="githi-v1", runtime="githi", status="active",
            created_at=datetime.now(UTC), updated_at=datetime.now(UTC),
        ))
    await db_session.commit()
    return made


@pytest.fixture
def api(db_session, owner, monkeypatch: pytest.MonkeyPatch):  # noqa: ANN001, ANN201
    def connect(supervisor: Handler) -> httpx.AsyncClient:
        settings = Settings(
            environment="development",
            secret_key="user-secret-key-also-at-least-32-bytes!!",
            ai_supervisor_base_url="http://supervisor.test",
            ai_service_jwt_secret="ai-service-jwt-secret-at-least-32-bytes!!",
        )
        monkeypatch.setattr(
            "prime_agent.services.session_service.AiClient.from_settings",
            lambda _s=None: AiClient(settings, transport=httpx.MockTransport(supervisor)),
        )
        app = create_app()

        async def override_db() -> AsyncIterator[object]:
            yield db_session

        app.dependency_overrides[get_db] = override_db
        app.dependency_overrides[get_current_user] = lambda: owner
        return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")

    return connect


async def _harnesses(db_session, harness_id: str) -> int:  # noqa: ANN001
    return int(await db_session.scalar(select(func.count()).select_from(Harness).where(Harness.id == harness_id)))


async def _links(db_session, harness_id: str) -> list[str]:  # noqa: ANN001
    rows = await db_session.scalars(select(SessionLink.ai_session_id).where(SessionLink.harness_id == harness_id))
    return sorted(rows)


async def test_its_sessions_are_erased_then_it_is_deleted(db_session, harness, api) -> None:  # noqa: ANN001
    harness_id = harness.id
    erased: list[str] = []

    def supervisor(request: httpx.Request) -> httpx.Response:
        assert request.method == "DELETE" and request.url.path.endswith("/data")
        erased.append(request.url.path.split("/")[3])
        # One was erased already (by hand, or a deletion that failed part way): done too.
        return httpx.Response(404 if request.url.path.split("/")[3] == "ai-1" else 204)

    async with api(supervisor) as client:
        assert (await client.delete(f"/api/v1/harnesses/{harness_id}")).status_code == 204
    assert sorted(erased) == ["ai-0", "ai-1", "ai-2"]
    assert await _harnesses(db_session, harness_id) == 0
    assert await _links(db_session, harness_id) == []


async def test_while_a_session_cannot_be_erased_the_harness_is_kept(db_session, harness, api) -> None:  # noqa: ANN001
    harness_id = harness.id

    def supervisor(request: httpx.Request) -> httpx.Response:
        stuck = request.url.path.split("/")[3] == "ai-2"
        return httpx.Response(503 if stuck else 204, json={"detail": "still stopping"})

    async with api(supervisor) as client:
        answer = await client.delete(f"/api/v1/harnesses/{harness_id}")
        assert answer.status_code == 503
    assert await _harnesses(db_session, harness_id) == 1
    assert await _links(db_session, harness_id) == ["ai-2"]  # the others are gone already

    async with api(lambda _request: httpx.Response(204)) as client:  # deleting again finishes
        assert (await client.delete(f"/api/v1/harnesses/{harness_id}")).status_code == 204
    assert await _harnesses(db_session, harness_id) == 0
    assert await _links(db_session, harness_id) == []
