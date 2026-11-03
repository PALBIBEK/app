"""A journal database for tests that must hold on SQLite and on Postgres, where writers
really run at once (set SUPERVISOR_TEST_PG_URL; CI does). Use ``factory`` as a fixture."""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from supervisor.db.engine import open_db

type Factory = async_sessionmaker[AsyncSession]
PG_URL = os.environ.get("SUPERVISOR_TEST_PG_URL", "")


@pytest_asyncio.fixture(
    params=["sqlite", pytest.param("postgres", marks=pytest.mark.skipif(not PG_URL, reason="no Postgres"))]
)
async def factory(request: pytest.FixtureRequest, tmp_path: Path) -> AsyncIterator[Factory]:
    if request.param == "sqlite":
        async with open_db(f"sqlite+aiosqlite:///{tmp_path / 'j.db'}") as (_, made):
            yield made
        return
    name = f"journal_{uuid.uuid4().hex[:10]}"
    admin = create_async_engine(PG_URL, isolation_level="AUTOCOMMIT")
    async with admin.connect() as conn:
        await conn.execute(text(f'CREATE DATABASE "{name}"'))
    try:
        async with open_db(f"{PG_URL.rpartition('/')[0]}/{name}") as (_, made):
            yield made
    finally:
        async with admin.connect() as conn:
            await conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        await admin.dispose()
