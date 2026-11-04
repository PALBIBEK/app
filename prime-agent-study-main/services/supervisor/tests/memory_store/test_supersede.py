"""Supersede is one transaction: the old item is checked and marked with the new one
written, so a failed supersede leaves nothing behind and one item never has two
current successors."""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import func, select

from supervisor.memory_service.app import _map_error
from supervisor.memory_service.models import MemoryItemRow
from supervisor.memory_service.service import MemoryService
from supervisor.memory_service.tier2_types import ItemNotFound, ItemSuperseded, NewMemoryItem
from supervisor.memory_service.types import Scope

SC = Scope("t1", "user:alice", "agent")


async def _svc(url: str) -> MemoryService:
    svc = MemoryService(database_url=url)
    await svc.startup()
    return svc


async def _count(svc: MemoryService) -> int:
    async with svc._factory() as db:
        return int(await db.scalar(select(func.count()).select_from(MemoryItemRow)) or 0)


async def _current(svc: MemoryService) -> list[str]:
    return sorted(r.content for r in await svc._bank.list_active(SC))


@pytest.mark.asyncio
async def test_superseding_a_missing_item_writes_nothing(url: str) -> None:
    svc = await _svc(url)
    with pytest.raises(ItemNotFound):
        await svc.supersede("no-such-item", NewMemoryItem(content="replacement"), SC)
    assert await _count(svc) == 0
    await svc.aclose()


@pytest.mark.asyncio
async def test_another_scopes_item_is_not_found(url: str) -> None:
    svc = await _svc(url)
    theirs = await svc.write(Scope("t1", "user:bob", "agent"), NewMemoryItem(content="bob's"))
    with pytest.raises(ItemNotFound):
        await svc.supersede(theirs, NewMemoryItem(content="mine"), SC)
    assert await _count(svc) == 1
    await svc.aclose()


@pytest.mark.asyncio
async def test_a_superseded_item_cannot_be_superseded_again(url: str) -> None:
    svc = await _svc(url)
    v1 = await svc.write(SC, NewMemoryItem(content="v1"))
    await svc.supersede(v1, NewMemoryItem(content="v2"), SC)
    with pytest.raises(ItemSuperseded):
        await svc.supersede(v1, NewMemoryItem(content="v2-other"), SC)
    assert await _current(svc) == ["v2"]
    await svc.aclose()


@pytest.mark.asyncio
async def test_concurrent_supersedes_of_one_item_leave_one_successor(url: str) -> None:
    svc = await _svc(url)
    v1 = await svc.write(SC, NewMemoryItem(content="v1"))
    out = await asyncio.gather(
        *(svc.supersede(v1, NewMemoryItem(content=f"v2-{i}"), SC) for i in range(5)),
        return_exceptions=True,
    )
    won = [o for o in out if isinstance(o, str)]
    assert len(won) == 1
    assert all(isinstance(o, ItemSuperseded) for o in out if not isinstance(o, str))
    assert len(await _current(svc)) == 1
    assert await _count(svc) == 2, "the losers wrote nothing"
    await svc.aclose()


def test_the_errors_are_answers_not_internal_errors() -> None:
    assert _map_error(ItemNotFound("ITEM_NOT_FOUND: x")).status_code == 404
    assert _map_error(ItemSuperseded("ITEM_SUPERSEDED: x")).status_code == 409
