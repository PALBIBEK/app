"""Bank recall ranks like the systems it follows (mem0, Generative Agents, LangChain's
time-weighted retriever): unrelated items are excluded, relevance outweighs recency."""

from __future__ import annotations

import time

import pytest
from sqlalchemy import select, text, update

from supervisor.memory_service.embed import EMBED_DIM, HashingEmbedder, cosine
from supervisor.memory_service.models import MemoryItemRow
from supervisor.memory_service.recall import _normalized
from supervisor.memory_service.service import MemoryService
from supervisor.memory_service.tier2_types import NewMemoryItem, TokenBudget
from supervisor.memory_service.types import Scope

SC = Scope("t1", "user:alice", "agent")
DAY_MS = 86_400_000


async def _svc(url: str) -> MemoryService:
    svc = MemoryService(database_url=url)
    await svc.startup()
    return svc


async def _age(svc: MemoryService, item_id: str, days: float) -> None:
    async with svc._factory() as db, db.begin():
        at = int(time.time() * 1000 - days * DAY_MS)
        await db.execute(update(MemoryItemRow).where(MemoryItemRow.id == item_id).values(valid_from_ms=at))


@pytest.mark.asyncio
async def test_a_relevant_old_item_beats_fresh_unrelated_ones(url: str) -> None:
    svc = await _svc(url)
    old = await svc.write(SC, NewMemoryItem(content="the billing service is deployed with helm on cluster east-2"))
    await _age(svc, old, 40)
    for i in range(6):
        await svc.write(SC, NewMemoryItem(content=f"lunch order number {i} was a sandwich with soup"))
    got = await svc.recall(SC, "how is the billing service deployed", TokenBudget(max_tokens=40))
    assert [i.content for i in got.items] == ["the billing service is deployed with helm on cluster east-2"]
    await svc.aclose()


@pytest.mark.asyncio
async def test_nothing_relevant_recalls_nothing(url: str) -> None:
    svc = await _svc(url)
    for i in range(6):
        await svc.write(SC, NewMemoryItem(content=f"lunch order number {i} was a sandwich with soup"))
    got = await svc.recall(SC, "what is the capital of peru", TokenBudget(max_tokens=512))
    assert got.items == ()
    await svc.aclose()


@pytest.mark.asyncio
async def test_among_relevant_items_newer_and_more_confident_rank_higher(url: str) -> None:
    svc = await _svc(url)
    older = await svc.write(SC, NewMemoryItem(content="deploys go out on friday afternoons", confidence=0.8))
    await _age(svc, older, 30)
    newer = await svc.write(SC, NewMemoryItem(content="deploys go out on monday mornings", confidence=0.8))
    got = await svc.recall(SC, "when do deploys go out", TokenBudget(max_tokens=512))
    assert [i.id for i in got.items] == [newer, older]
    await svc.aclose()


@pytest.mark.asyncio
async def test_a_smaller_relevant_item_still_fits_after_a_large_one_does_not(url: str) -> None:
    svc = await _svc(url)
    await svc.write(SC, NewMemoryItem(content="billing deploy runbook " + "step detail " * 80, confidence=1.0))
    small = await svc.write(SC, NewMemoryItem(content="billing deploys use helm", confidence=0.1))
    got = await svc.recall(SC, "billing deploy", TokenBudget(max_tokens=20))
    assert [i.id for i in got.items] == [small]
    await svc.aclose()


def test_function_words_no_longer_make_unrelated_texts_similar() -> None:
    e = HashingEmbedder()
    assert len(e.embed("x")) == EMBED_DIM == 1024
    assert cosine(e.embed("how is the billing service deployed"), e.embed("the cat is asleep")) == 0.0
    assert cosine(e.embed("billing service deploys"), e.embed("the billing service")) > 0.5


def test_normalization_follows_generative_agents() -> None:
    assert _normalized({"a": 1.0, "b": 3.0, "c": 2.0}) == {"a": 0.0, "b": 1.0, "c": 0.5}
    assert _normalized({"a": 0.4, "b": 0.4}) == {"a": 0.5, "b": 0.5}


@pytest.mark.asyncio
async def test_items_embedded_at_the_old_size_are_re_embedded_on_startup(url: str) -> None:
    svc = await _svc(url)
    item = await svc.write(SC, NewMemoryItem(content="the billing service uses helm"))
    async with svc._factory() as db, db.begin():  # as a database from before migration 5 holds it
        await db.execute(update(MemoryItemRow).where(MemoryItemRow.id == item).values(embedding=[0.125] * 64))
        await db.execute(text("DELETE FROM memory_schema_version WHERE version = 5"))
    await svc.aclose()
    again = await _svc(url)
    async with again._factory() as db:
        stored = (await db.scalars(select(MemoryItemRow.embedding).where(MemoryItemRow.id == item))).one()
    assert stored == HashingEmbedder().embed("the billing service uses helm")
    got = await again.recall(SC, "billing helm", TokenBudget(max_tokens=100))
    assert [i.id for i in got.items] == [item]
    await again.aclose()
