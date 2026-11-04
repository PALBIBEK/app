"""Notebook rules: an archived notebook is read-only and out of bank recall, an edit may
not blank a fact, the same fact typed in different Unicode forms is one fact, and a
notebook's key is validated by the service itself."""

from __future__ import annotations

import unicodedata
from collections.abc import AsyncIterator

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import select

from supervisor.memory_service.models import MemoryItemRow
from supervisor.memory_service.notebooks.errors import EmptyFact, NotebookArchived
from supervisor.memory_service.notebooks.types import CaptureMetadata, Edit, Sensitivity
from supervisor.seams.services.memory_service_client import memory_service_credential
from tests.support.notebooks import MemoryServiceHandle, memory_service

pytestmark = pytest.mark.asyncio

KEY = {"tenant_id": "t1", "owner": "alice", "agent_pin": "pin-1", "name": "default"}
OWN = CaptureMetadata(sensitivity=Sensitivity.ORDINARY, inherited=())


@pytest_asyncio.fixture
async def svc(url: str, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[MemoryServiceHandle]:
    monkeypatch.setenv("SERVICE_JWT_SECRET", "notebook-rules-tests-service-jwt-secret!!")
    monkeypatch.delenv("MEMORY_SERVICE_JWT_SECRET", raising=False)
    monkeypatch.delenv("MEMORY_SERVICE_TOKEN", raising=False)
    async with memory_service(url) as handle:
        yield handle


async def _live_items(svc: MemoryServiceHandle) -> list[str]:
    async with svc.app.state.memory._factory() as db:
        rows = await db.scalars(select(MemoryItemRow).where(MemoryItemRow.forgotten == 0))
        return sorted(r.content for r in rows)


async def test_an_archived_notebook_is_read_only_and_out_of_bank_recall(svc: MemoryServiceHandle) -> None:
    client = svc.client()
    nb = await client.ensure(**KEY)
    await client.capture(nb.id, ["prefers tea"], context=OWN)
    assert await _live_items(svc) == ["prefers tea"]
    await client.set_archived(nb.id, True)
    assert await _live_items(svc) == [], "archived facts leave the bank"
    fact = (await client.facts(nb.id))[0]
    for change in (
        client.capture(nb.id, ["written after archive"]),
        client.edit(nb.id, [Edit(fact.id, "prefers coffee")]),
        client.delete(nb.id, [fact.id]),
        client.clear(nb.id),
        client.restore(nb.id, 0),
    ):
        with pytest.raises(NotebookArchived):
            await change
    assert [f.text for f in await client.facts(nb.id)] == ["prefers tea"], "still readable"
    revision = (await client.get(nb.id)).revision
    await client.set_archived(nb.id, False)
    assert await _live_items(svc) == ["prefers tea"], "unarchived facts are indexed again"
    assert (await client.get(nb.id)).revision == revision, "archiving changes no fact"
    assert await client.capture(nb.id, ["prefers green tea"], context=OWN) == 1


async def test_an_edit_may_not_blank_a_fact(svc: MemoryServiceHandle) -> None:
    client = svc.client()
    nb = await client.ensure(**KEY)
    await client.capture(nb.id, ["keep me"])
    fact = (await client.facts(nb.id))[0]
    for blank in ("   ", "- ", "(2026-01-02) "):
        with pytest.raises(EmptyFact):
            await client.edit(nb.id, [Edit(fact.id, blank)])
    assert [f.text for f in await client.facts(nb.id)] == ["keep me"]


async def test_the_same_fact_in_another_unicode_form_or_spacing_is_one_fact(svc: MemoryServiceHandle) -> None:
    client = svc.client()
    nb = await client.ensure(**KEY)
    composed = unicodedata.normalize("NFC", "Lives in Zürich")
    decomposed = unicodedata.normalize("NFD", "Lives in Zürich")
    assert await client.capture(nb.id, [composed, decomposed, "lives  in   ZÜRICH"]) == 1
    assert [f.text for f in await client.facts(nb.id)] == [composed]
    await client.capture(nb.id, [unicodedata.normalize("NFD", "Café au lait")])
    assert (await client.facts(nb.id))[1].text == unicodedata.normalize("NFC", "Café au lait"), "stored composed"


async def test_a_notebook_key_is_validated(svc: MemoryServiceHandle) -> None:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=svc.app),
        base_url="http://memory.test",
        headers={"Authorization": f"Bearer {memory_service_credential('t1')}"},
    ) as http:
        for bad in ({"name": "n" * 65}, {"name": ""}, {"name": "   "}, {"owner": ""}, {"label": "l" * 201}):
            r = await http.post("/v1/notebooks:ensure", json={**KEY, **bad})
            assert r.status_code == 422, (bad, r.text)
        ok = await http.post("/v1/notebooks:ensure", json={**KEY, "name": "n" * 64})
        assert ok.status_code in (200, 201), ok.text
