"""On Postgres with pgvector, bank recall ranks the nearest CANDIDATES of the scope found
in the database (mem0's pgvector store; pgvector's exact search through a filter index);
elsewhere it ranks every live item."""

from __future__ import annotations

import pytest
from sqlalchemy import text

from supervisor.memory_service import vector_search
from supervisor.memory_service.embed import HashingEmbedder
from supervisor.memory_service.service import MemoryService
from supervisor.memory_service.tier2_types import ForgetSelector, NewMemoryItem, TokenBudget
from supervisor.memory_service.types import Scope

SC = Scope("t1", "user:alice", "agent")
OTHER = Scope("t1", "user:bob", "agent")


async def _svc(url: str) -> MemoryService:
    svc = MemoryService(database_url=url)
    await svc.startup()
    return svc


async def _has_pgvector(svc: MemoryService) -> bool:
    async with svc._factory() as db:
        return await vector_search.available(db)


@pytest.mark.asyncio
async def test_sqlite_ranks_every_live_item(url: str) -> None:
    if not url.startswith("sqlite"):
        pytest.skip("the scan path")
    svc = await _svc(url)
    await svc.write(SC, NewMemoryItem(content="billing deploys use helm"))
    async with svc._factory() as db:
        assert not await vector_search.available(db)
    rows = await svc._bank.candidates(SC, HashingEmbedder().embed("billing"))
    assert [r.content for r in rows] == ["billing deploys use helm"]
    await svc.aclose()


@pytest.mark.asyncio
async def test_postgres_finds_the_nearest_live_items_of_the_scope_in_the_database(url: str) -> None:
    svc = await _svc(url)
    if not await _has_pgvector(svc):
        pytest.skip("needs Postgres with pgvector")
    wanted = await svc.write(SC, NewMemoryItem(content="the billing service is deployed with helm"))
    for i in range(150):
        await svc.write(SC, NewMemoryItem(content=f"standup note {i} about the sprint board"))
    await svc.write(OTHER, NewMemoryItem(content="the billing service is deployed with helm"))
    gone = await svc.write(SC, NewMemoryItem(content="billing deployed by hand"))
    await svc.forget(SC, ForgetSelector(item_id=gone), actor="t", reason="test")
    old = await svc.write(SC, NewMemoryItem(content="billing deployed with ansible"))
    await svc.supersede(old, NewMemoryItem(content="billing deployed with helm charts"), SC)

    async def no_scan(scope: Scope) -> list[object]:
        raise AssertionError("with pgvector recall must not load every item")

    svc._bank.list_active = no_scan  # type: ignore[method-assign]
    rows = await svc._bank.candidates(SC, HashingEmbedder().embed("how is the billing service deployed"))
    assert len(rows) == vector_search.CANDIDATES
    assert rows[0].id == wanted, "the nearest first"
    assert {(r.tenant, r.subject) for r in rows} == {("t1", "user:alice")}, "only the scope"
    assert not {gone, old} & {r.id for r in rows}, "only live items"
    got = await svc.recall(SC, "how is the billing service deployed", TokenBudget(max_tokens=64))
    assert got.items[0].id == wanted
    await svc.aclose()


@pytest.mark.asyncio
async def test_the_vector_column_follows_the_json_embedding_and_backfills(url: str) -> None:
    svc = await _svc(url)
    if not await _has_pgvector(svc):
        pytest.skip("needs Postgres with pgvector")
    item = await svc.write(SC, NewMemoryItem(content="billing deploys use helm"))
    async with svc._engine.begin() as conn:  # a database from before the column
        await conn.execute(text("DROP TRIGGER memory_item_embedding_vec ON memory_item"))
        await conn.execute(text("ALTER TABLE memory_item DROP COLUMN embedding_vec"))
    await svc.aclose()
    again = await _svc(url)
    async with again._factory() as db:
        backfilled = await db.scalar(text("SELECT embedding_vec IS NOT NULL FROM memory_item WHERE id = :i"), {"i": item})
        assert backfilled is True
        await db.execute(text("UPDATE memory_item SET embedding = CAST(:e AS json) WHERE id = :i"), {"e": "[1.0]", "i": item})
        await db.commit()
        follows = await db.scalar(text("SELECT embedding_vec IS NULL FROM memory_item WHERE id = :i"), {"i": item})
    assert follows is True, "a vector of another size is left out, as the scan would score it 0"
    await again.aclose()
