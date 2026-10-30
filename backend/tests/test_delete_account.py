"""Deleting an account erases it everywhere: the supervisor first, then every row of it here.

There was no way to: a person could not remove their account, nor anything it held.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import event, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from prime_agent.config import Settings
from prime_agent.db import Base
from prime_agent.db.models.harness import Harness
from prime_agent.db.models.refresh_token import RefreshToken
from prime_agent.db.models.session_link import SessionLink
from prime_agent.db.models.user import User
from prime_agent.main import create_app
from prime_agent.services.ai_client import AiClient
from prime_agent.services.auth_service import AuthService
from prime_agent.services.harness_service import HarnessService
from prime_agent.web.dependencies import get_db
from tests.conftest import PASSWORD

pytestmark = pytest.mark.asyncio

ORIGIN = {"Origin": "http://localhost:3000"}
Handler = Callable[[httpx.Request], httpx.Response]


@pytest.fixture
async def db() -> AsyncIterator[AsyncSession]:
    """A database that enforces foreign keys, as Postgres does: the cascades are real."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)

    @event.listens_for(engine.sync_engine, "connect")
    def _foreign_keys(connection, _record) -> None:  # noqa: ANN001
        connection.execute("PRAGMA foreign_keys=ON")

    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        yield session
    await engine.dispose()


@pytest.fixture
async def people(db: AsyncSession) -> dict[str, User]:
    """Alice, with a harness, a session and a sign-in; Bob, with a harness of his own."""
    made: dict[str, User] = {}
    for name in ("alice", "bob"):
        user = User(email=f"{name}@example.com", name=name, hashed_password=AuthService.hash_password(PASSWORD))
        db.add(user)
        await db.commit()
        harness = await HarnessService.acreate(
            {"name": f"{name}-harness", "model": "m", "mode": "agent", "tools": [], "memory": [],
             "user_id": user.id, "created_by": user.id},
            db,
        )
        db.add(SessionLink(
            id=str(uuid4()), user_id=user.id, harness_id=harness.id, ai_session_id=f"ai-{name}",
            tenant_id=user.id, harness_version="githi-v1", runtime="githi", status="active",
            created_at=datetime.now(UTC), updated_at=datetime.now(UTC),
        ))
        await db.commit()
        made[name] = user
    return made


@pytest.fixture
def api(db: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> Callable[[Handler], httpx.AsyncClient]:
    def connect(supervisor: Handler, *, base_url: str | None = "http://supervisor.test") -> httpx.AsyncClient:
        settings = Settings(
            environment="development",
            secret_key="user-secret-key-also-at-least-32-bytes!!",
            ai_supervisor_base_url=base_url,
            ai_service_jwt_secret="ai-service-jwt-secret-at-least-32-bytes!!",
        )
        monkeypatch.setattr(
            "prime_agent.web.routes.users.AiClient.from_settings",
            lambda _settings: AiClient(settings, transport=httpx.MockTransport(supervisor)),
        )
        app = create_app()

        async def override_db() -> AsyncIterator[AsyncSession]:
            yield db

        app.dependency_overrides[get_db] = override_db
        return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")

    return connect


async def _signed_in(db: AsyncSession, user: User) -> dict[str, str]:
    tokens = await AuthService.issue_tokens(user, db)
    return {**ORIGIN, "Authorization": f"Bearer {tokens['access_token']}"}


async def _delete(client: httpx.AsyncClient, headers: dict[str, str], password: str = PASSWORD) -> httpx.Response:
    return await client.request("DELETE", "/api/v1/users/me", json={"password": password}, headers=headers)


async def _rows(db: AsyncSession, row: type, user_id: str) -> int:
    return int(await db.scalar(select(func.count()).select_from(row).where(row.user_id == user_id)) or 0)


async def test_the_account_and_everything_of_it_goes(db: AsyncSession, people: dict[str, User], api) -> None:
    alice, bob = people["alice"], people["bob"]
    alice_id, bob_id = alice.id, bob.id
    calls: list[httpx.Request] = []

    def supervisor(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json={"sessions": 1, "scopes": 1, "scopes_handed_on": 0,
                                         "workspace_files": 2, "secrets": 0, "notebooks": 1})

    headers = await _signed_in(db, alice)
    async with api(supervisor) as client:
        answer = await _delete(client, headers)
        assert answer.status_code == 204, answer.text
        assert "max-age=0" in answer.headers["set-cookie"].lower()  # signed out here too
        assert (await client.get("/api/v1/users/me", headers=headers)).status_code == 401

    (erase,) = calls
    assert (erase.method, erase.url.path) == ("DELETE", "/v1/principals/me/data")
    db.expire_all()
    assert await db.get(User, alice_id) is None
    for row in (Harness, SessionLink, RefreshToken):
        assert await _rows(db, row, alice_id) == 0, row.__tablename__
    assert await db.get(User, bob_id) is not None
    assert await _rows(db, Harness, bob_id) == await _rows(db, SessionLink, bob_id) == 1


async def test_the_wrong_password_deletes_nothing(db: AsyncSession, people: dict[str, User], api) -> None:
    alice = people["alice"]
    calls: list[httpx.Request] = []
    headers = await _signed_in(db, alice)
    async with api(lambda request: calls.append(request) or httpx.Response(200, json={})) as client:
        answer = await _delete(client, headers, password="not-the-password")
    assert answer.status_code == 400
    assert calls == []
    assert await db.get(User, alice.id) is not None


async def test_when_the_supervisor_cannot_erase_the_account_is_kept(
    db: AsyncSession, people: dict[str, User], api
) -> None:
    alice = people["alice"]
    alice_id = alice.id
    headers = await _signed_in(db, alice)
    async with api(lambda _request: httpx.Response(503, json={"detail": "a session is still stopping"})) as client:
        answer = await _delete(client, headers)
        assert answer.status_code == 503
        assert "account was kept" in answer.json()["detail"]["message"]
        assert (await client.get("/api/v1/users/me", headers=headers)).status_code == 200
    db.expire_all()
    assert await _rows(db, SessionLink, alice_id) == 1


async def test_without_a_supervisor_there_is_nothing_to_erase_there(
    db: AsyncSession, people: dict[str, User], api
) -> None:
    alice = people["alice"]
    alice_id = alice.id
    headers = await _signed_in(db, alice)
    async with api(lambda _request: pytest.fail("no supervisor is configured"), base_url=None) as client:
        assert (await _delete(client, headers)).status_code == 204
    db.expire_all()
    assert await db.get(User, alice_id) is None
