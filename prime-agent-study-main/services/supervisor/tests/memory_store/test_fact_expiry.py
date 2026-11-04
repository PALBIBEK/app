"""Time-bound facts (mem0's expiration_date): after their day they are kept and shown where
facts are managed, but the agent no longer recalls, searches or reads them."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from sqlalchemy import select, text

from supervisor.memory.binding import MemoryBinding
from supervisor.memory.cross import recall_across
from supervisor.memory.disclosure import Audience
from supervisor.memory.service import CaptureContext, NotebookMemory
from supervisor.memory.tools import TurnMemory
from supervisor.memory_service.models import MemoryItemRow
from supervisor.memory_service.notebooks.errors import InvalidExpiry
from supervisor.memory_service.notebooks.models import FactRow
from supervisor.memory_service.notebooks.render import fact_line
from supervisor.memory_service.notebooks.types import (
    CaptureMetadata,
    Edit,
    RecallContext,
    Sensitivity,
)
from tests.support.notebooks import MemoryServiceHandle, memory_service

pytestmark = pytest.mark.asyncio

KEY = {"tenant_id": "t1", "owner": "alice", "agent_pin": "pin-1", "name": "default"}
OWN = CaptureMetadata(sensitivity=Sensitivity.ORDINARY, inherited=())


@pytest_asyncio.fixture
async def svc(url: str, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[MemoryServiceHandle]:
    monkeypatch.setenv("SERVICE_JWT_SECRET", "fact-expiry-tests-service-jwt-secret!!")
    monkeypatch.delenv("MEMORY_SERVICE_JWT_SECRET", raising=False)
    monkeypatch.delenv("MEMORY_SERVICE_TOKEN", raising=False)
    async with memory_service(url) as handle:
        yield handle


async def _bank(svc: MemoryServiceHandle) -> list[str]:
    async with svc.app.state.memory._factory() as db:
        rows = await db.scalars(select(MemoryItemRow.content).where(MemoryItemRow.forgotten == 0))
        return sorted(rows)


async def test_an_expired_fact_is_kept_but_not_recalled_searched_or_read(svc: MemoryServiceHandle) -> None:
    client = svc.client()
    nb = await client.ensure(**KEY)
    await client.capture(nb.id, ["Prefers tea"], context=OWN)
    past = CaptureMetadata(sensitivity=Sensitivity.ORDINARY, inherited=(), expires_on="2020-01-31")
    await client.capture(nb.id, ["Is on leave until January 31"], context=past)
    future = CaptureMetadata(sensitivity=Sensitivity.ORDINARY, inherited=(), expires_on="2999-12-31")
    await client.capture(nb.id, ["Works on the migration"], context=future)

    held = await client.facts(nb.id)
    assert [(f.text, f.expires_on, f.expired()) for f in held] == [
        ("Prefers tea", None, False),
        ("Is on leave until January 31", "2020-01-31", True),
        ("Works on the migration", "2999-12-31", False),
    ], "kept, with its expiry, where facts are managed"
    assert fact_line(held[2]).endswith(", until 2999-12-31) Works on the migration")

    memory = NotebookMemory(client)
    recalled = " ".join(r.text for r in await recall_across(memory, [nb.id], RecallContext(query="leave tea")))
    assert "Prefers tea" in recalled and "leave" not in recalled
    assert await memory.query(nb.id, "leave") == []
    assert "leave" not in await memory.read(nb.id)
    assert await _bank(svc) == ["Prefers tea"], "a time-bound fact stays out of the bank (it has no expiry)"


async def test_an_edit_sets_keeps_or_clears_an_expiry(svc: MemoryServiceHandle) -> None:
    client = svc.client()
    nb = await client.ensure(**KEY)
    await client.capture(nb.id, ["Is on leave"], context=OWN)
    (fact,) = await client.facts(nb.id)
    await client.edit(nb.id, [Edit(fact.id, "Is on leave")], context=CaptureMetadata(expires_on="2999-01-02"))
    assert (await client.facts(nb.id))[0].expires_on == "2999-01-02"
    await client.edit(nb.id, [Edit(fact.id, "Is on parental leave")])  # no context: expiry kept
    assert [(f.text, f.expires_on) for f in await client.facts(nb.id)] == [("Is on parental leave", "2999-01-02")]
    await client.edit(nb.id, [Edit(fact.id, "Is on parental leave")], context=CaptureMetadata(expires_on=""))
    assert (await client.facts(nb.id))[0].expires_on is None
    with pytest.raises(InvalidExpiry):
        await client.edit(nb.id, [Edit(fact.id, "x")], context=CaptureMetadata(expires_on="next friday"))
    with pytest.raises(InvalidExpiry):
        await client.capture(nb.id, ["y"], context=CaptureMetadata(expires_on="31/01/2027"))
    assert [f.text for f in await client.facts(nb.id)] == ["Is on parental leave"], "nothing changed"


async def test_the_memory_tool_sets_and_clears_expiry(svc: MemoryServiceHandle) -> None:
    client = svc.client()
    nb = await client.ensure(**KEY)
    binding = MemoryBinding(owner="alice", agent_pin="pin-1", write=nb.id, read=(nb.id,))
    tools = TurnMemory(NotebookMemory(client), binding, Audience.private("alice"), "s1", dependencies=())
    added = await tools.call("memory", {"action": "add", "facts": [{"text": "Is travelling"}], "expires": "2999-03-01"})
    assert added.text == "Remembered 1 fact."
    (fact,) = await client.facts(nb.id)
    assert fact.expires_on == "2999-03-01"
    bad = await tools.call("memory", {"action": "add", "facts": [{"text": "x"}], "expires": "soon"})
    assert bad.is_error and "YYYY-MM-DD" in bad.text
    await tools.call("memory", {"action": "edit", "facts": [{"id": fact.id, "text": "Is travelling"}], "expires": "never"})
    assert (await client.facts(nb.id))[0].expires_on is None


async def test_existing_facts_migrate_with_no_expiry(svc: MemoryServiceHandle) -> None:
    client = svc.client()
    nb = await client.ensure(**KEY)
    await client.capture(nb.id, ["Prefers tea"], context=OWN)
    service = svc.app.state.memory
    async with service._engine.begin() as conn:  # a database from before migration 6
        await conn.execute(text("ALTER TABLE memory_facts DROP COLUMN expires_on"))
        await conn.execute(text("DELETE FROM memory_schema_version WHERE version = 6"))
    await service.startup()
    async with service._factory() as db:
        assert list(await db.scalars(select(FactRow.expires_on))) == [None]
    assert [f.expires_on for f in await client.facts(nb.id)] == [None]


def test_capture_context_carries_expiry_to_the_service() -> None:
    from supervisor.memory_service.notebooks import wire

    sent = wire.context_out(CaptureContext(expires_on="2999-01-01"))
    assert sent is not None and sent["expires_on"] == "2999-01-01"
    assert wire.context_in(sent) == CaptureMetadata(expires_on="2999-01-01")
