"""Shared helpers for independent D4 BFF acceptance."""

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
from prime_agent.db.models.user import User
from prime_agent.main import create_app
from prime_agent.services.ai_client import AiClient
from prime_agent.services.harness_bind_service import SHIPPED_COMPOSITION_PINS
from prime_agent.services.platform_status_service import PlatformStatusService
from prime_agent.services.session_service import SessionService
from prime_agent.web.dependencies import get_current_user, get_db

DIGEST = SHIPPED_COMPOSITION_PINS["coding-v1"]["composition_digest"]
SECRET = "d4-acceptance-ai-jwt-secret-at-least-32-bytes!!"


@pytest.fixture
def d4_settings(monkeypatch: pytest.MonkeyPatch) -> Settings:
    settings = Settings(
        environment="development",
        secret_key="d4-user-secret-key-also-at-least-32-bytes!!",
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
    return await make_user(email="d4-owner@example.com", name="D4 Owner")


@pytest.fixture
async def other_user(make_user) -> User:
    return await make_user(email="d4-other@example.com", name="D4 Other")


async def make_harness(
    db: AsyncSession,
    user: User,
    *,
    name: str | None = None,
    bound: bool = True,
) -> Harness:
    pin = SHIPPED_COMPOSITION_PINS["coding-v1"]
    h = Harness(
        id=str(uuid4()),
        user_id=user.id,
        name=name or f"d4-harness-{uuid4().hex[:8]}",
        model="claude-sonnet-4",
        mode="sticky",
        tools=[],
        memory=[],
        bind_status="bound" if bound else "unbound",
        harness_version=pin["harness_version"] if bound else None,
        runtime=pin["runtime"] if bound else None,
        composition_id="coding-v1" if bound else None,
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
    """Wire MockTransport into AiClient / SessionService / bind / platform."""
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
        "prime_agent.web.routes.sessions.SessionService.from_settings",
        lambda s=None: SessionService(settings, client=make_client(settings)),
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


def catalog_and_ready_handler(
    *,
    ready: bool = True,
    create_session_id: str | None = None,
) -> Callable[[httpx.Request], httpx.Response]:
    """Minimal supervisor mock: catalog digests + readyz + session create."""
    ai_sid = create_session_id or f"ai-{uuid4().hex}"

    catalog_body = {
        "kind": "composition",
        "artifact_id": "coding-v1",
        "digest": DIGEST,
        "status": "offered",
        "semver": "1.0.0",
    }

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/readyz"):
            if ready:
                return httpx.Response(200, json={"status": "ready"})
            return httpx.Response(
                503, json={"status": "not_ready", "reason": "draining"}
            )
        if "/v1/catalog/" in path:
            if path.rstrip("/").endswith("/compositions"):
                return httpx.Response(200, json=[catalog_body])
            return httpx.Response(200, json=catalog_body)
        if request.method == "POST" and path.rstrip("/").endswith("/v1/sessions"):
            return httpx.Response(
                201,
                json={
                    "session_id": ai_sid,
                    "harness_version": "coding-v1",
                    "runtime": "harness",
                    "status": "active",
                },
            )
        if request.method == "POST" and path.endswith("/messages"):
            return httpx.Response(
                202, json={"turn_id": f"turn-{uuid4().hex}", "status": "accepted"}
            )
        if request.method == "POST" and path.endswith("/abort"):
            return httpx.Response(200, json={"status": "ok"})
        if request.method == "DELETE" and "/v1/sessions/" in path:
            return httpx.Response(204)
        if request.method == "POST" and path.endswith("/fork"):
            return httpx.Response(201, json={"session_id": f"ai-fork-{uuid4().hex}"})
        if request.method == "GET" and "/events" in path:
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                content=b'id: 1\ndata: {"seq":1,"event_id":"e1","type":"run_start","ts_ms":1,"data":{}}\n\n',
            )
        if request.method == "GET" and "/v1/sessions/" in path:
            return httpx.Response(
                200,
                json={
                    "session_id": ai_sid,
                    "status": "active",
                    "harness_version": "coding-v1",
                    "runtime": "harness",
                    "last_seq": 0,
                    "message_count": 0,
                    "total_cost": 0.0,
                },
            )
        return httpx.Response(404, json={"detail": "unhandled mock path"})

    return handler
