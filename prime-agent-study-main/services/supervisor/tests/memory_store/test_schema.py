"""The memory database is migrated and checked at startup: a column a model gained
reaches an existing database, and a missing one stops startup instead of the first query."""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from supervisor.memory_service import schema
from supervisor.memory_service.service import MemoryService


async def _start(url: str) -> MemoryService:
    svc = MemoryService(database_url=url)
    await svc.startup()
    return svc


async def _versions(url: str) -> list[int]:
    engine = create_async_engine(url)
    async with engine.connect() as conn:
        rows = (await conn.execute(text("SELECT version FROM memory_schema_version ORDER BY version"))).all()
    await engine.dispose()
    return [int(r[0]) for r in rows]


@pytest.mark.asyncio
async def test_a_new_database_gets_the_schema_and_its_version(url: str) -> None:
    svc = await _start(url)
    await svc.aclose()
    assert await _versions(url) == [v for v, _, _ in schema.MIGRATIONS]
    again = await _start(url)  # a second start changes nothing
    await again.aclose()
    assert await _versions(url) == [v for v, _, _ in schema.MIGRATIONS]


@pytest.mark.asyncio
async def test_a_missing_column_stops_startup_and_says_which(url: str) -> None:
    svc = await _start(url)
    async with svc._engine.begin() as conn:
        await conn.execute(text('ALTER TABLE memory_notebooks DROP COLUMN agent'))  # a database from before
    await svc.aclose()
    with pytest.raises(schema.SchemaOutOfDate, match="memory_notebooks.agent"):
        await _start(url)


@pytest.mark.asyncio
async def test_a_migration_adds_the_column_to_an_existing_database(url: str, monkeypatch: pytest.MonkeyPatch) -> None:
    svc = await _start(url)
    async with svc._engine.begin() as conn:
        await conn.execute(text('ALTER TABLE memory_notebooks DROP COLUMN agent'))
    await svc.aclose()
    monkeypatch.setattr(
        schema, "MIGRATIONS", [*schema.MIGRATIONS, (99, "notebooks.agent", schema.add_column("memory_notebooks", "agent", "TEXT"))]
    )
    again = await _start(url)
    notebook = await again.notebooks.store.ensure(tenant_id="t1", owner="a", agent_pin="p", name="n", agent="agent-x")
    assert notebook.agent == "agent-x"
    await again.aclose()
    assert 99 in await _versions(url)


@pytest.mark.asyncio
async def test_replicas_starting_together_migrate_once(url: str) -> None:
    if url.startswith("sqlite"):
        pytest.skip("concurrent starts are Postgres's case")
    started = await asyncio.gather(*(_start(url) for _ in range(5)))
    for svc in started:
        await svc.aclose()
    assert await _versions(url) == [v for v, _, _ in schema.MIGRATIONS]
