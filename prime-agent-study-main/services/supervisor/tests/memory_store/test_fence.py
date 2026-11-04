"""The lease fence is checked in the flush's own transaction, under the session row lock:
a writer whose lease is taken over after its early check still cannot write."""

from __future__ import annotations

import pytest
from sqlalchemy import update

from supervisor.memory_service.models import MemorySessionRow
from supervisor.memory_service.service import MemoryService
from supervisor.memory_service.types import Scope, StaleFence

SC = Scope("t1", "user:alice", "agent")


@pytest.mark.asyncio
async def test_a_takeover_after_the_early_check_still_stops_the_old_writer(url: str) -> None:
    svc = MemoryService(database_url=url)
    await svc.startup()
    h = await svc.create_session(SC)
    old = await svc.acquire_lease(h.id, "worker-a")
    svc.append(h.id, "user/message", {"text": "from the old owner"})
    early = svc._leases.assert_fence

    async def check_then_lose_the_lease(session_id: str, fence: int | None) -> None:
        await early(session_id, fence)  # passes: worker-a still holds the lease
        async with svc._factory() as db, db.begin():  # then it expires and worker-b takes over
            await db.execute(
                update(MemorySessionRow).where(MemorySessionRow.id == session_id).values(lease_until_ms=0)
            )
        await svc.acquire_lease(session_id, "worker-b")

    svc._leases.assert_fence = check_then_lose_the_lease  # type: ignore[method-assign]
    with pytest.raises(StaleFence):
        await svc.flush(h.id, fence=old)
    svc._leases.assert_fence = early  # type: ignore[method-assign]
    said = [e.data.get("text") for e in (await svc.load(h.id)).events]
    assert "from the old owner" not in said
    await svc.aclose()


@pytest.mark.asyncio
async def test_the_current_owner_and_an_unleased_session_still_flush(url: str) -> None:
    svc = MemoryService(database_url=url)
    await svc.startup()
    leased = await svc.create_session(SC)
    fence = await svc.acquire_lease(leased.id, "worker-a")
    svc.append(leased.id, "user/message", {"text": "owner"})
    assert await svc.flush(leased.id, fence=fence) == 0
    free = await svc.create_session(SC)
    svc.append(free.id, "user/message", {"text": "no lease"})
    assert await svc.flush(free.id) == 0
    await svc.aclose()
