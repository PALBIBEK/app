"""Shared helpers for independent D6 BFF / UUID→ToolSet acceptance."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Any
from uuid import uuid4

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from prime_agent.config import Settings, get_settings
from prime_agent.db.models.harness import Harness
from prime_agent.db.models.tool import Tool
from prime_agent.db.models.user import User
from prime_agent.main import create_app
from prime_agent.services.ai_client import AiClient
from tests.support.catalog import DIGEST, PIN
from prime_agent.services.platform_status_service import PlatformStatusService
from prime_agent.web.dependencies import get_current_user, get_db

SECRET = "d6-acceptance-ai-jwt-secret-at-least-32-bytes!!"


@pytest.fixture
def d6_settings(monkeypatch: pytest.MonkeyPatch) -> Settings:
    settings = Settings(
        environment="development",
        secret_key="d6-user-secret-key-also-at-least-32-bytes!!",
        ai_supervisor_base_url="http://supervisor.test",
        ai_service_jwt_secret=SECRET,
        ai_platform_status_cache_ttl_seconds=0,
    )
    get_settings.cache_clear()
    PlatformStatusService.clear_cache()
    for target in (
        "prime_agent.config.get_settings",
        "prime_agent.config.settings.get_settings",
        "prime_agent.web.routes.sessions.get_settings",
        "prime_agent.services.session_service.get_settings",
        "prime_agent.services.harness_bind_service.get_settings",
        "prime_agent.services.platform_status_service.get_settings",
        "prime_agent.services.ai_client.get_settings",
        "prime_agent.security.service_jwt.get_settings",
        "prime_agent.security.jwt_manager.get_settings",
    ):
        monkeypatch.setattr(target, lambda s=settings: s)
    yield settings
    get_settings.cache_clear()
    PlatformStatusService.clear_cache()


@pytest.fixture
async def owner(make_user) -> User:
    return await make_user(email="d6-owner@example.com", name="D6 Owner")


@pytest.fixture
async def other_user(make_user) -> User:
    return await make_user(email="d6-other@example.com", name="D6 Other")


async def make_tool(
    db: AsyncSession,
    user: User,
    *,
    name: str | None = None,
    category: str = "custom",
    endpoint: str | None = "https://api.example.com/d6-tool",
    status: str = "active",
) -> Tool:
    tool = Tool(
        id=str(uuid4()),
        user_id=user.id,
        name=name or f"d6_tool_{uuid4().hex[:8]}",
        description="D6 acceptance custom tool",
        category=category,
        status=status,
        parameters=[],
        endpoint=endpoint,
        auth_required=False,
    )
    db.add(tool)
    await db.commit()
    await db.refresh(tool)
    return tool


async def make_harness(
    db: AsyncSession,
    user: User,
    *,
    tools: list[str] | None = None,
    bound: bool = True,
) -> Harness:
    pin = PIN
    h = Harness(
        id=str(uuid4()),
        user_id=user.id,
        name=f"d6-harness-{uuid4().hex[:8]}",
        model="claude-sonnet-4",
        mode="sticky",
        tools=list(tools or []),
        memory=[],
        bind_status="bound" if bound else "unbound",
        harness_version=pin["harness_version"] if bound else None,
        runtime=pin["runtime"] if bound else None,
        composition_id="githi-v1" if bound else None,
        composition_digest=DIGEST if bound else None,
    )
    db.add(h)
    await db.commit()
    await db.refresh(h)
    return h


def patch_ai_transport(
    monkeypatch: pytest.MonkeyPatch,
    settings: Settings,
    handler: Callable[[httpx.Request], httpx.Response],
) -> list[httpx.Request]:
    captured: list[httpx.Request] = []

    def wrapping(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return handler(request)

    def make_client(s: Settings | None = None) -> AiClient:
        return AiClient(s or settings, transport=httpx.MockTransport(wrapping))

    monkeypatch.setattr(
        "prime_agent.services.session_service.AiClient.from_settings",
        make_client,
    )
    monkeypatch.setattr(
        "prime_agent.services.harness_bind_service.AiClient.from_settings",
        make_client,
    )
    monkeypatch.setattr(
        "prime_agent.services.platform_status_service.AiClient.from_settings",
        make_client,
    )
    monkeypatch.setattr(
        "prime_agent.services.tool_publish_service.AiClient.from_settings",
        make_client,
        raising=False,
    )
    monkeypatch.setattr(
        "prime_agent.web.routes.sessions.SessionService.from_settings",
        lambda s=None: __import__(
            "prime_agent.services.session_service", fromlist=["SessionService"]
        ).SessionService(settings, client=make_client(settings)),
    )
    return captured


@asynccontextmanager
async def as_user(
    db_session: AsyncSession, user: User
) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app()

    async def override_db():
        yield db_session

    async def override_user():
        return user

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = override_user
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
