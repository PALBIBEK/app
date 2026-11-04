"""A side effect in the outbox runs once however many executors race for it: an item is
claimed before it runs. A failed executor releases it; a crashed one's claim expires."""

from __future__ import annotations

import asyncio
import time
from typing import Any

import pytest
from sqlalchemy import text, update

from supervisor.memory_service import outbox as outbox_mod
from supervisor.memory_service.models import OutboxRow
from supervisor.memory_service.service import MemoryService
from supervisor.memory_service.types import Scope

SC = Scope("t1", "user:alice", "agent")


async def _session_with_effect(url: str) -> tuple[MemoryService, str]:
    svc = MemoryService(database_url=url)
    await svc.startup()
    h = await svc.create_session(SC)
    svc.append(h.id, "assistant/message", {"text": "paying"})
    svc.append(h.id, "tool/call", {"call_id": "c1", "name": "pay", "side_effect": True, "idempotency_key": "k1"})
    await svc.flush(h.id)
    return svc, h.id


@pytest.mark.asyncio
async def test_executors_racing_for_an_item_run_it_once(url: str) -> None:
    svc, sid = await _session_with_effect(url)
    calls: list[str] = []

    async def pay(payload: dict[str, Any]) -> dict[str, Any]:
        calls.append(payload["call_id"])
        await asyncio.sleep(0.05)
        return {"paid": True}

    results = await asyncio.gather(*(svc.execute_outbox(sid, pay) for _ in range(5)))
    assert calls == ["c1"]
    assert sum(len(r) for r in results) == 1
    events = (await svc.load(sid)).events
    assert [e.type for e in events].count("tool/result") == 1
    await svc.aclose()


@pytest.mark.asyncio
async def test_a_failed_executor_releases_the_item(url: str) -> None:
    svc, sid = await _session_with_effect(url)

    async def broken(payload: dict[str, Any]) -> dict[str, Any]:
        raise RuntimeError("payment provider down")

    with pytest.raises(RuntimeError):
        await svc.execute_outbox(sid, broken)
    assert await svc.execute_outbox(sid, lambda p: {"paid": True}) == [{"paid": True}]
    await svc.aclose()


@pytest.mark.asyncio
async def test_a_crashed_executors_claim_expires(url: str) -> None:
    svc, sid = await _session_with_effect(url)

    async def claimed(at: int) -> None:  # an executor claimed it at ``at`` and its process died
        async with svc._factory() as db, db.begin():
            await db.execute(update(OutboxRow).values(status="running", claimed_at=at))

    now_ms = int(time.time() * 1000)
    await claimed(now_ms)
    assert await svc.execute_outbox(sid, lambda p: {"paid": True}) == [], "a fresh claim is held"
    await claimed(now_ms - outbox_mod.CLAIM_TIMEOUT_MS - 1)
    assert await svc.execute_outbox(sid, lambda p: {"paid": True}) == [{"paid": True}]
    await svc.aclose()


@pytest.mark.asyncio
async def test_an_existing_database_gets_the_claim_column(url: str) -> None:
    svc = MemoryService(database_url=url)
    await svc.startup()
    async with svc._engine.begin() as conn:
        await conn.execute(text("ALTER TABLE outbox DROP COLUMN claimed_at"))
        await conn.execute(text("DELETE FROM memory_schema_version WHERE version = 2"))
    await svc.aclose()
    again = MemoryService(database_url=url)
    await again.startup()  # migration 2 adds it back
    async with again._engine.connect() as conn:
        await conn.execute(text("SELECT claimed_at FROM outbox"))
    await again.aclose()
    assert outbox_mod.CLAIM_TIMEOUT_MS > 0
