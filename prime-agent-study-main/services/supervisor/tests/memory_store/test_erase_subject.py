"""Erasing a subject deletes what was kept about them: forgetting hides an item from recall,
erasing removes it. Their bank items, session events, archives, outbox and extraction jobs go;
the audit keeps ids and counts."""

from __future__ import annotations

import pytest
from sqlalchemy import func, select

from supervisor.memory_service.models import (
    ExtractionJobRow,
    ForgetAuditRow,
    MemoryItemRow,
    OutboxRow,
    SessionArchiveRow,
    SessionEventRow,
)
from supervisor.memory_service.service import MemoryService
from supervisor.memory_service.tier2_types import ForgetSelector, NewMemoryItem
from supervisor.memory_service.types import Scope, SessionNotFound

ALICE = Scope("t1", "user:alice", "agent")
BOB = Scope("t1", "user:bob", "agent")


async def _count(svc: MemoryService, table: type, **where: object) -> int:
    async with svc._factory() as db:
        query = select(func.count()).select_from(table)
        for col, value in where.items():
            query = query.where(getattr(table, col) == value)
        return int(await db.scalar(query) or 0)


@pytest.mark.asyncio
async def test_erasing_a_subject_deletes_their_content_and_only_theirs(url: str) -> None:
    svc = MemoryService(database_url=url)
    await svc.startup()
    first = await svc.write(ALICE, NewMemoryItem(content="alice prefers tea"))
    await svc.supersede(first, NewMemoryItem(content="alice prefers coffee"), ALICE)
    hidden = await svc.write(ALICE, NewMemoryItem(content="alice's forgotten detail"))
    await svc.forget(ALICE, ForgetSelector(item_id=hidden), actor="alice", reason="asked")
    await svc.write(BOB, NewMemoryItem(content="bob prefers juice"))
    nb = await svc.notebooks.store.ensure(tenant_id="t1", owner="alice", agent_pin="p", name="default")
    await svc.notebooks.capture(nb.id, ["alice lives in Pune"])

    archived = await svc.create_session(ALICE)
    svc.append(archived.id, "user/message", {"text": "I prefer dark mode always"})
    svc.append(archived.id, "turn/end", {})
    await svc.flush(archived.id)  # an extraction job too
    await svc.archive_session(archived.id)
    live = await svc.create_session(ALICE)
    svc.append(live.id, "assistant/message", {"text": "paying"})
    svc.append(live.id, "tool/call", {"call_id": "c", "name": "pay", "side_effect": True})
    await svc.flush(live.id)  # an outbox row
    svc.append(live.id, "user/message", {"text": "buffered, not yet flushed"})

    erased = await svc.erase_subject(tenant="t1", subject="user:alice", actor="ops", reason="erasure request")

    assert erased["notebooks_erased"] == 1 and erased["items_forgotten"] == 3
    assert await _count(svc, MemoryItemRow, subject="user:alice") == 0, "forgotten ones included"
    assert await _count(svc, MemoryItemRow, subject="user:bob") == 1
    for table in (SessionEventRow, OutboxRow, ExtractionJobRow, SessionArchiveRow):
        for sid in (archived.id, live.id):
            assert await _count(svc, table, session_id=sid) == 0, table.__tablename__
    for sid in (archived.id, live.id):
        with pytest.raises(SessionNotFound):
            await svc.load(sid)
    with pytest.raises(SessionNotFound):
        await svc.flush(live.id)  # what was buffered is not written into an erased session
    assert await _count(svc, SessionEventRow, session_id=live.id) == 0
    async with svc._factory() as db:
        audit = (await db.execute(select(ForgetAuditRow).where(ForgetAuditRow.kind == "erase_subject"))).scalar_one()
    assert audit.count == 3 and "tea" not in str(audit.item_ids)
    await svc.aclose()
