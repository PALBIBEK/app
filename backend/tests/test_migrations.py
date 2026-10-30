"""The product's schema is its migrations, applied at boot.

The migrations could not run (their base revision was never committed, and there was no
Alembic environment); development made the schema with ``create_all``, which never alters
a table, and production made none. Runs against Postgres when BACKEND_TEST_PG_URL names a
server (CI sets it); each test uses a fresh database on it.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from collections.abc import AsyncIterator

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Connection, inspect, text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from prime_agent.db import Base, models  # noqa: F401  (every table on Base.metadata)
from prime_agent.db.migrate import alembic_config, legacy_revision, migrate

PG_URL = os.environ.get("BACKEND_TEST_PG_URL", "")
pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(not PG_URL, reason="BACKEND_TEST_PG_URL not set"),
]


@pytest.fixture
async def engine() -> AsyncIterator[AsyncEngine]:
    name = f"mig_{uuid.uuid4().hex[:10]}"
    admin = create_async_engine(PG_URL, isolation_level="AUTOCOMMIT")
    async with admin.connect() as conn:
        await conn.execute(text(f'CREATE DATABASE "{name}"'))
    fresh = create_async_engine(f"{PG_URL.rpartition('/')[0]}/{name}")
    yield fresh
    await fresh.dispose()
    async with admin.connect() as conn:
        await conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
    await admin.dispose()


async def _drift(engine: AsyncEngine) -> list[object]:
    def diff(conn: Connection) -> list[object]:
        return list(compare_metadata(MigrationContext.configure(conn, opts={"compare_type": True}), Base.metadata))

    async with engine.connect() as conn:
        return await conn.run_sync(diff)


async def _revision(engine: AsyncEngine) -> str | None:
    async with engine.connect() as conn:
        return await conn.run_sync(lambda c: MigrationContext.configure(c).get_current_revision())


async def _alembic(engine: AsyncEngine, action: str, revision: str) -> None:
    def run(conn: Connection) -> None:
        config = alembic_config()
        config.attributes["connection"] = conn
        getattr(command, action)(config, revision)

    async with engine.connect() as conn:
        await conn.run_sync(run)
        await conn.commit()


async def test_the_migrations_and_the_models_describe_the_same_schema(engine: AsyncEngine) -> None:
    await migrate(engine)
    assert await _drift(engine) == []


async def test_many_replicas_migrate_one_database_at_once(engine: AsyncEngine) -> None:
    url = engine.url.render_as_string(hide_password=False)
    replicas = [create_async_engine(url) for _ in range(4)]
    try:
        await asyncio.gather(*(migrate(r) for r in replicas))
        await migrate(replicas[0])  # and again: nothing left to do
    finally:
        for replica in replicas:
            await replica.dispose()
    assert await _drift(engine) == []


async def test_a_database_made_by_create_all_is_adopted_and_brought_up_to_date(engine: AsyncEngine) -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)  # what development did before
    await migrate(engine)
    assert await _revision(engine) is not None
    assert await _drift(engine) == []


async def test_an_older_database_without_history_gets_what_it_lacks(engine: AsyncEngine) -> None:
    """Made by an older build's create_all: it has the harness releases, not the trace tables."""
    await _alembic(engine, "upgrade", "e5f6a7b8c9d0")
    async with engine.begin() as conn:
        await conn.execute(text("DROP TABLE alembic_version"))
        await conn.run_sync(lambda c: Base.metadata.tables["environments"].create(c))  # create_all made it
        assert await conn.run_sync(lambda c: legacy_revision(inspect(c))) == "e5f6a7b8c9d0"

    await migrate(engine)

    assert await _drift(engine) == []
    async with engine.connect() as conn:
        assert await conn.run_sync(lambda c: inspect(c).has_table("trace_proposals"))


async def test_a_new_database_holds_no_revision_until_migrated(engine: AsyncEngine) -> None:
    async with engine.connect() as conn:
        assert await conn.run_sync(lambda c: legacy_revision(inspect(c))) is None
    await migrate(engine)
    assert await _revision(engine) == ScriptDirectory.from_config(alembic_config()).get_current_head()
