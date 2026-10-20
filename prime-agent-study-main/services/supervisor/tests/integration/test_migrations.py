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


async def test_retiring_the_pi_harness_keeps_boot_and_closes_its_sessions(fresh_db: str) -> None:
    """0017: a registered ``harness``-runtime version is gone, its sessions failed, compositions deprecated."""
    from alembic import command

    from supervisor.db.migrate import alembic_config
    from supervisor.db.models import CatalogArtifactRow, HarnessVersionRow, SessionRow

    def to(revision: str):  # type: ignore[no-untyped-def]
        def run(conn: Connection) -> None:
            config = alembic_config()
            config.attributes["connection"] = conn
            command.upgrade(config, revision)

        return run

    engine = make_engine(fresh_db)
    try:
        async with engine.begin() as conn:
            await conn.run_sync(to("0016_vault"))
            await conn.execute(
                HarnessVersionRow.__table__.insert().values(
                    harness_version="my-pi", protocol_version=1, runtime="harness", manifest={}
                )
            )
            for sid, runtime, status in (("s-pi", "harness", "active"), ("s-old", "harness", "terminated"),
                                         ("s-githi", "githi", "active")):
                await conn.execute(SessionRow.__table__.insert().values(id=sid, runtime=runtime, status=status))
            for name, harness in (("pi-comp", "my-pi"), ("shipped-pi", "coding-v1"), ("g", "githi-v1")):
                await conn.execute(
                    CatalogArtifactRow.__table__.insert().values(
                        kind="composition", artifact_id=name, digest="sha256:" + "0" * 64,
                        body={"harness_version": harness},
                    )
                )
            await conn.run_sync(to("head"))
        async with engine.connect() as conn:
            versions = (await conn.execute(text("SELECT harness_version FROM harness_versions"))).all()
            sessions = dict((await conn.execute(text("SELECT id, status FROM sessions"))).all())
            comps = dict((await conn.execute(text("SELECT artifact_id, status FROM catalog_artifacts"))).all())
        assert versions == []
        assert sessions == {"s-pi": "failed", "s-old": "terminated", "s-githi": "active"}
        assert comps == {"pi-comp": "deprecated", "shipped-pi": "deprecated", "g": "offered"}
    finally:
        await engine.dispose()
