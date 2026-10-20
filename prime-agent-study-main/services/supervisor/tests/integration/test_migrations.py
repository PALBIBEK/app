"""The schema is the migrations, applied at boot by every process at once without a race.

Runs against Postgres when SUPERVISOR_TEST_PG_URL names a server (CI sets it);
each test uses a fresh database on it.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from collections.abc import AsyncIterator

import pytest
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from sqlalchemy import Connection, text
from sqlalchemy.ext.asyncio import create_async_engine

from supervisor.db.engine import create_all, make_engine
from supervisor.db.migrate import LegacySchemaError, migrate
from supervisor.db.models import Base

PG_URL = os.environ.get("SUPERVISOR_TEST_PG_URL", "")
pytestmark = pytest.mark.skipif(not PG_URL, reason="SUPERVISOR_TEST_PG_URL not set")


@pytest.fixture
async def fresh_db() -> AsyncIterator[str]:
    name = f"mig_{uuid.uuid4().hex[:10]}"
    admin = create_async_engine(PG_URL, isolation_level="AUTOCOMMIT")
    async with admin.connect() as conn:
        await conn.execute(text(f'CREATE DATABASE "{name}"'))
    base, _, _ = PG_URL.rpartition("/")
    yield f"{base}/{name}"
    async with admin.connect() as conn:
        await conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
    await admin.dispose()


async def test_many_processes_migrate_one_database_at_once(fresh_db: str) -> None:
    engines = [make_engine(fresh_db) for _ in range(4)]
    try:
        await asyncio.gather(*(migrate(e) for e in engines))
        await migrate(engines[0])  # and again: nothing left to do
    finally:
        for engine in engines:
            await engine.dispose()


async def test_the_migrations_and_the_models_describe_the_same_schema(fresh_db: str) -> None:
    engine = make_engine(fresh_db)
    try:
        await migrate(engine)

        def diff(conn: Connection) -> list[object]:
            return list(compare_metadata(MigrationContext.configure(conn), Base.metadata))

        async with engine.connect() as conn:
            assert await conn.run_sync(diff) == []
    finally:
        await engine.dispose()


async def test_a_database_without_migration_history_is_refused_not_guessed(fresh_db: str) -> None:
    engine = make_engine(fresh_db)
    try:
        await create_all(engine)  # what builds before migrations did
        with pytest.raises(LegacySchemaError):
            await migrate(engine)
    finally:
        await engine.dispose()
