"""G2 at the platform's concurrency (``docs/memory-gaps.md``, hot spot S1): automatic
captures into one notebook from many sessions, on memory-service replicas sharing one
Postgres, queue without its lock and land once each.

Before G2 every capture took the notebook's row lock, so a notebook many sessions capture
into (one owner's many sessions, a shared notebook) served its captures one at a time, each
waiting for the rest. Now a capture is one insert into the inbox: it never waits on the
notebook's lock, even one another transaction holds, and the inbox's drainers land the
queue in a few locked batches.
"""

from __future__ import annotations

import asyncio
import time

import pytest
from sqlalchemy import update
from sqlalchemy.ext.asyncio import create_async_engine

from supervisor.memory_service.notebooks.models import NotebookRow
from supervisor.memory_service.notebooks.types import CaptureMetadata, Fact, Sensitivity
from supervisor.memory_service.observations import PROMPT_VERSION, ObservationDraft, ObservationType
from tests.support.memory_pg import Replicas, run_workers

pytestmark = pytest.mark.asyncio

PIN = "composition:githi@sha256:inbox-race"
OWN = CaptureMetadata(sensitivity=Sensitivity.ORDINARY, inherited=())
REPLICAS = 4
CAPTURES = 100


def observation(statement: str) -> ObservationDraft:
    return ObservationDraft(
        observation_type=ObservationType.PROJECT,
        topic_hint="services",
        statement=statement,
        keywords=(),
        aliases=(),
        extraction_model="model",
        prompt_version=PROMPT_VERSION,
        created_at=1,
        body=None,
    )


@pytest.fixture(autouse=True)
def _burst_allowed(monkeypatch: pytest.MonkeyPatch) -> None:
    """These captures arrive at once from one tenant: room for them in each replica's
    rate limit (``memory_service.rate_limit``), which is not what is under test here."""
    monkeypatch.setenv("MEMORY_RATE_LIMIT_BURST", str(CAPTURES))


async def test_captures_into_one_locked_notebook_queue_without_waiting_and_land_once(replicas: Replicas) -> None:
    await replicas.start(REPLICAS)
    clients = replicas.clients(CAPTURES)
    nb = await clients[0].ensure(tenant_id="t1", owner="alice", agent_pin=PIN, name="default")
    engine = create_async_engine(replicas.url)
    try:
        async with engine.connect() as holder:
            # The notebook's lock, held open by another transaction (an edit, a promotion).
            await holder.execute(update(NotebookRow).where(NotebookRow.id == nb.id).values(revision=NotebookRow.revision))
            began = time.monotonic()
            queued = await asyncio.wait_for(
                run_workers(
                    CAPTURES,
                    lambda i: clients[i].observe(
                        nb.id, [observation(f"Service {i:03d} owns a queue")], context=OWN, idempotency_key=f"burst-{i}"
                    ),
                ),
                30,
            )
            waited = time.monotonic() - began
            assert queued == [1] * CAPTURES, "every capture queued while the notebook was locked"
            assert await clients[0].facts(nb.id) == [], "none landed past the held lock"
            await holder.rollback()
    finally:
        await engine.dispose()

    facts = await _landed(clients[0], nb.id, CAPTURES)
    assert sorted(f.text for f in facts) == sorted(f"Service {i:03d} owns a queue" for i in range(CAPTURES))
    assert all(f.typed is not None and f.typed.kind == "project" for f in facts)
    revisions = {r.revision for r in await clients[0].history(nb.id)}
    assert len(revisions) < CAPTURES / 4, f"landed in batches, not a lock a capture ({len(revisions)} revisions)"
    assert waited < 15, f"queuing {CAPTURES} captures took {waited:.1f}s"


async def test_a_capture_retried_on_another_replica_lands_once(replicas: Replicas) -> None:
    """The same burst queued by two replicas at once (Temporal retried the capture
    elsewhere): one row, one fact, one revision."""
    started = await replicas.start(2)
    nb = await started[0].client().ensure(tenant_id="t1", owner="alice", agent_pin=PIN, name="default")

    queued = await run_workers(
        8,
        lambda i: started[i % 2].client().observe(
            nb.id, [observation("Deploys go through the staging flag")], context=OWN, idempotency_key="burst-x"
        ),
    )

    assert sorted(queued) == [0] * 7 + [1]
    (fact,) = await _landed(started[1].client(), nb.id, 1)
    assert fact.text == "Deploys go through the staging flag"
    assert len(await started[0].client().history(nb.id)) == 1


async def _landed(client: object, notebook_id: str, count: int) -> list[Fact]:
    async with asyncio.timeout(30):
        while len(facts := await client.facts(notebook_id)) < count:  # type: ignore[attr-defined]
            await asyncio.sleep(0.05)
    return facts


async def test_a_capture_into_a_notebook_erased_under_it_is_not_found_not_a_duplicate(replicas: Replicas) -> None:
    """The insert fails the notebook's foreign key: that is a missing notebook, never a
    retried burst already queued (which would report the capture handled and lose it)."""
    (replica,) = await replicas.start(1)
    store = replica.app.state.memory.notebooks.store
    from supervisor.memory_service.notebooks.errors import NotebookNotFound
    from supervisor.memory_service.notebooks.types import Provenance

    with pytest.raises(NotebookNotFound):
        await store.append_inbox(
            "t1~gone", [], Provenance(Sensitivity.ORDINARY, (), False), author=None, expires_on=None,
            idempotency_key="burst-1",
        )


async def test_erasing_an_owner_while_their_captures_queue_never_fails_on_the_queue(replicas: Replicas) -> None:
    """Erasure locks the owner's notebooks first: a capture racing it waits, then finds no
    notebook, and never commits a queued row between the erasure's deletes (which made the
    erasure fail on the inbox's foreign key and leave the owner's data in place)."""
    from supervisor.memory_service.notebooks.errors import NotebookNotFound

    started = await replicas.start(2)
    for round_ in range(5):
        nb = await started[0].client().ensure(tenant_id="t1", owner="alice", agent_pin=PIN, name=f"r{round_}")
        store = started[1].app.state.memory.notebooks.store

        async def capture(i: int, notebook_id: str = nb.id) -> str:
            try:
                await started[i % 2].client().observe(notebook_id, [observation(f"Racing {i}")], context=OWN)
            except NotebookNotFound:
                return "gone"
            return "queued"

        erased, *captures = await asyncio.gather(
            store.erase_owner(tenant_id="t1", owner="alice"), *(capture(i) for i in range(20))
        )
        assert erased >= 1, "the erasure went through"
        assert set(captures) <= {"queued", "gone"}
        assert await started[0].app.state.memory.notebooks.store.pending() == 0, "nothing of the owner left queued"
