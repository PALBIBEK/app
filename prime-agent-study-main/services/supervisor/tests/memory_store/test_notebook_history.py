"""Notebook history is read whole (in pages that never split a revision), and reading
or restoring at a revision the notebook has not reached is refused."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
import pytest_asyncio

from supervisor.memory.client import NotebookClient
from supervisor.memory_service.notebooks.errors import RevisionOutOfRange
from supervisor.memory_service.notebooks.ops import NotebookOps
from tests.support.notebooks import MemoryServiceHandle, memory_service

pytestmark = pytest.mark.asyncio

KEY = {"tenant_id": "t1", "owner": "alice", "agent_pin": "pin-1", "name": "default"}


@pytest_asyncio.fixture
async def svc(url: str, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[MemoryServiceHandle]:
    monkeypatch.setenv("SERVICE_JWT_SECRET", "notebook-history-tests-jwt-secret!!")
    monkeypatch.delenv("MEMORY_SERVICE_JWT_SECRET", raising=False)
    monkeypatch.delenv("MEMORY_SERVICE_TOKEN", raising=False)
    async with memory_service(url) as handle:
        yield handle


def _ops(svc: MemoryServiceHandle) -> NotebookOps:
    ops: NotebookOps = svc.app.state.memory.notebooks
    return ops


async def _three_revisions(client: NotebookClient) -> str:
    """Revision 1 adds three facts, 2 adds two, 3 clears all five: 10 rows."""
    nb = await client.ensure(**KEY)
    await client.capture(nb.id, ["a", "b", "c"])
    await client.capture(nb.id, ["d", "e"])
    await client.clear(nb.id)
    return nb.id


async def test_a_page_ends_on_a_whole_revision_so_paging_skips_nothing(svc: MemoryServiceHandle) -> None:
    client = svc.client()
    nb = await _three_revisions(client)
    first = await client.revisions(nb, after=0, limit=2)
    assert [r.revision for r in first] == [1, 1, 1], "revision 1 is finished, not cut at 2 rows"
    paged = await client.history(nb, page=2)
    assert [r.revision for r in paged] == [1, 1, 1, 2, 2, 3, 3, 3, 3, 3]
    assert paged == await client.revisions(nb, limit=100)
    assert await _ops(svc).store.history(nb, page=2) == await _ops(svc).store.history(nb)


async def test_history_past_one_page_is_not_cut_off(svc: MemoryServiceHandle) -> None:
    client = svc.client()
    nb = await client.ensure(**KEY)
    for i in range(12):
        await client.capture(nb.id, [f"fact {i}"])
    store = _ops(svc).store
    assert len(await store.history(nb.id, page=5)) == 12
    at_end = await _ops(svc).facts_at(nb.id, 12)
    assert [f.text for f in at_end][-1] == "fact 11"


async def test_reading_or_restoring_a_revision_that_does_not_exist_is_refused(
    svc: MemoryServiceHandle,
) -> None:
    client = svc.client()
    nb = await _three_revisions(client)
    ops = _ops(svc)
    for revision in (4, 999, -3):
        with pytest.raises(RevisionOutOfRange):
            await ops.facts_at(nb, revision)
        with pytest.raises(RevisionOutOfRange):
            await ops.restore(nb, revision)
        with pytest.raises(RevisionOutOfRange):  # over HTTP: a 422 the client rebuilds
            await client.restore(nb, revision)
    assert await client.facts(nb) == [], "nothing was restored"
    assert [f.text for f in await client.facts_at(nb, 2)] == ["a", "b", "c", "d", "e"]
    assert await client.facts_at(nb, 0) == []
    await client.restore(nb, 1)
    assert [f.text for f in await client.facts(nb)] == ["a", "b", "c"]
