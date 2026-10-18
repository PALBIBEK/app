"""Shared helpers for independent D7 outer BFF / honesty acceptance."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from uuid import uuid4

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from prime_agent.config import Settings, get_settings
from prime_agent.db.models.harness import Harness
from prime_agent.db.models.user import User
from prime_agent.main import create_app
from prime_agent.services.harness_bind_service import SHIPPED_COMPOSITION_PINS
from prime_agent.services.platform_status_service import PlatformStatusService
from prime_agent.web.dependencies import get_current_user, get_db

DIGEST = SHIPPED_COMPOSITION_PINS["coding-v1"]["composition_digest"]
SECRET = "d7-acceptance-ai-jwt-secret-at-least-32-bytes!!"


@pytest.fixture
def d7_settings(monkeypatch: pytest.MonkeyPatch) -> Settings:
    settings = Settings(
        environment="development",
        secret_key="d7-user-secret-key-also-at-least-32-bytes!!",
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
        "prime_agent.services.harness_service.get_settings",
    ):
        monkeypatch.setattr(target, lambda s=settings: s, raising=False)
    yield settings
    get_settings.cache_clear()
    PlatformStatusService.clear_cache()


@pytest.fixture
async def owner(make_user) -> User:
    return await make_user(email="d7-owner@example.com", name="D7 Owner")


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


async def make_harness(db: AsyncSession, user: User, *, memory: list[str] | None = None) -> Harness:
    pin = SHIPPED_COMPOSITION_PINS["coding-v1"]
    h = Harness(
        id=str(uuid4()),
        user_id=user.id,
        name=f"d7-harness-{uuid4().hex[:8]}",
        model="claude-sonnet-4",
        mode="sticky",
        tools=[],
        memory=list(memory or []),
        bind_status="bound",
        harness_version=pin["harness_version"],
        runtime=pin["runtime"],
        composition_id="coding-v1",
        composition_digest=DIGEST,
    )
    db.add(h)
    await db.commit()
    await db.refresh(h)
    return h
