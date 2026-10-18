"""A memory database per test: SQLite always, Postgres too when MEMORY_TEST_PG_URL is set.

Postgres is where row locks, identity columns and concurrent writers are real,
so the store's guarantees are checked against it, not only against SQLite.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from supervisor.db.engine import open_db

PG_URL = os.environ.get("MEMORY_TEST_PG_URL")
_MEMORY_TABLES = ("memory_fact_revisions", "memory_facts", "memory_notebooks")

BACKENDS = [
    "sqlite",
    pytest.param(
        "postgres", marks=pytest.mark.skipif(not PG_URL, reason="MEMORY_TEST_PG_URL not set")
    ),
]


@pytest_asyncio.fixture(params=BACKENDS)
async def memory_db(
    request: pytest.FixtureRequest, tmp_path: Path
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    url = PG_URL if request.param == "postgres" else f"sqlite+aiosqlite:///{tmp_path / 'm.db'}"
    assert url is not None
    async with open_db(url) as (engine, factory):
        if request.param == "postgres":
            async with engine.begin() as conn:
                await conn.execute(text(f"TRUNCATE {', '.join(_MEMORY_TABLES)} RESTART IDENTITY"))
        yield factory
