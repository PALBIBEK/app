"""Health endpoints and drain flag."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from supervisor.app import build_supervisor
from supervisor.db.engine import open_db
from supervisor.main import create_app
from tests.support.session_env import make_catalog


@pytest.mark.asyncio
async def test_healthz_ok(tmp_path) -> None:
    async with open_db(f"sqlite+aiosqlite:///{tmp_path / 'h.db'}") as (_, factory):
        state = build_supervisor(
            session_factory=factory,
            catalog=make_catalog(),
            blob_dir=tmp_path / "blobs",
        )
        app = create_app(state=state)
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            r = await client.get("/healthz")
            assert r.status_code == 200
            assert r.json()["status"] == "ok"


@pytest.mark.asyncio
async def test_readyz_ok(tmp_path) -> None:
    async with open_db(f"sqlite+aiosqlite:///{tmp_path / 'h.db'}") as (_, factory):
        state = build_supervisor(
            session_factory=factory,
            catalog=make_catalog(),
            blob_dir=tmp_path / "blobs",
        )
        app = create_app(state=state)
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            r = await client.get("/readyz")
            assert r.status_code == 200
            assert r.json()["status"] == "ready"


@pytest.mark.asyncio
async def test_readyz_503_while_draining(tmp_path) -> None:
    async with open_db(f"sqlite+aiosqlite:///{tmp_path / 'h.db'}") as (_, factory):
        state = build_supervisor(
            session_factory=factory,
            catalog=make_catalog(),
            blob_dir=tmp_path / "blobs",
        )
        state.draining = True
        app = create_app(state=state)
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            r = await client.get("/readyz")
            assert r.status_code == 503
            assert r.json()["reason"] == "draining"


@pytest.mark.asyncio
async def test_readyz_503_on_db_failure(tmp_path) -> None:
    async with open_db(f"sqlite+aiosqlite:///{tmp_path / 'h.db'}") as (_, factory):
        state = build_supervisor(
            session_factory=factory,
            catalog=make_catalog(),
            blob_dir=tmp_path / "blobs",
        )

        async def bad_db() -> bool:
            return False

        state.check_db = bad_db
        app = create_app(state=state)
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            r = await client.get("/readyz")
            assert r.status_code == 503
            assert r.json()["reason"] == "database"
