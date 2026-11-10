"""Typed observations through the memory service's inbox (G2 of ``docs/memory-gaps.md``):
an automatic capture is queued without the notebook's lock and lands as typed facts when
the inbox is drained (``notebooks.inbox``), through the client the supervisor uses."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import replace
from pathlib import Path

import httpx
import pytest
import pytest_asyncio

from supervisor.memory.client import NotebookClient
from supervisor.memory_service.notebooks.errors import InvalidObservation, NotebookArchived
from supervisor.memory_service.notebooks.types import CaptureMetadata, Fact, Sensitivity
from supervisor.memory_service.observations import PROMPT_VERSION, ObservationDraft, ObservationType
from tests.support.notebooks import MemoryServiceHandle, memory_service

pytestmark = pytest.mark.asyncio

PIN = "composition:githi@sha256:inbox"
OWN = CaptureMetadata(sensitivity=Sensitivity.ORDINARY, inherited=())


def draft(statement: str, kind: ObservationType = ObservationType.PROJECT, **changes: object) -> ObservationDraft:
    made = ObservationDraft(
        observation_type=kind,
        topic_hint="build",
        statement=statement,
        keywords=("make", "check"),
        aliases=("the build",),
        extraction_model="model-x",
        prompt_version=PROMPT_VERSION,
        created_at=1_700_000_000,
        body="Run it from the repository root.",
    )
    return replace(made, **changes)  # type: ignore[arg-type]


@pytest_asyncio.fixture
async def svc(tmp_path: Path) -> AsyncIterator[MemoryServiceHandle]:
    async with memory_service(f"sqlite+aiosqlite:///{tmp_path / 'memory.db'}") as handle:
        yield handle


async def landed(client: NotebookClient, notebook_id: str, count: int) -> list[Fact]:
    """The notebook's facts once ``count`` of them have landed (the drainer runs at once)."""
    async with asyncio.timeout(10):
        while len(facts := await client.facts(notebook_id)) < count:
            await asyncio.sleep(0.02)
    return facts


async def test_an_observation_lands_as_a_typed_fact(svc: MemoryServiceHandle) -> None:
    client = svc.client()
    nb = await client.ensure(tenant_id="t1", owner="alice", agent_pin=PIN, name="default")

    assert await client.observe(nb.id, [draft("The repository builds with make check")], author="agent", context=OWN) == 1

    (fact,) = await landed(client, nb.id, 1)
    assert fact.text == "The repository builds with make check"
    assert fact.author == "agent" and fact.provenance.sensitivity is Sensitivity.ORDINARY
    assert fact.typed is not None
    assert (fact.typed.kind, fact.typed.topic, fact.typed.keywords, fact.typed.aliases) == (
        "project", "build", ("make", "check"), ("the build",)
    )
    assert fact.typed.body == "Run it from the repository root."
    assert (fact.typed.extraction_model, fact.typed.prompt_version) == ("model-x", PROMPT_VERSION)


async def test_a_retried_capture_is_queued_once(svc: MemoryServiceHandle) -> None:
    client = svc.client()
    nb = await client.ensure(tenant_id="t1", owner="alice", agent_pin=PIN, name="default")

    first = await client.observe(nb.id, [draft("Deploys need the staging flag")], context=OWN, idempotency_key="burst-1")
    again = await client.observe(nb.id, [draft("Deploys need the staging flag")], context=OWN, idempotency_key="burst-1")

    assert (first, again) == (1, 0)
    await landed(client, nb.id, 1)
    assert len(await client.history(nb.id)) == 1, "one insert, one revision"


async def test_an_observation_out_of_bounds_refuses_its_whole_capture(svc: MemoryServiceHandle) -> None:
    client = svc.client()
    nb = await client.ensure(tenant_id="t1", owner="alice", agent_pin=PIN, name="default")

    with pytest.raises(InvalidObservation, match="statement"):
        await client.observe(nb.id, [draft("fine"), draft("x" * 1_025)], context=OWN)

    await svc.app.state.memory.inbox.drain()
    assert await client.facts(nb.id) == [], "neither the bad one nor the good one beside it"


async def test_an_archived_notebook_takes_no_observations(svc: MemoryServiceHandle) -> None:
    client = svc.client()
    nb = await client.ensure(tenant_id="t1", owner="alice", agent_pin=PIN, name="default")
    await client.set_archived(nb.id, True)

    with pytest.raises(NotebookArchived):
        await client.observe(nb.id, [draft("Too late")], context=OWN)


async def test_captures_queued_before_an_archive_are_dropped_not_kept(svc: MemoryServiceHandle) -> None:
    memory = svc.app.state.memory
    await memory.stop_background()  # hold the drainer: the archive comes between queue and drain
    client = svc.client()
    nb = await client.ensure(tenant_id="t1", owner="alice", agent_pin=PIN, name="default")
    await client.observe(nb.id, [draft("Queued then archived")], context=OWN)
    await client.set_archived(nb.id, True)

    assert await memory.inbox.drain() == 1
    assert await client.facts(nb.id) == [] and await memory.notebooks.store.pending() == 0


async def test_a_repeat_tightens_a_plain_fact_and_gives_it_its_type(svc: MemoryServiceHandle) -> None:
    """An observation that repeats a fact captured as plain text adds nothing new; the fact
    gains the type it lacked, without a revision (what it says did not change)."""
    client = svc.client()
    nb = await client.ensure(tenant_id="t1", owner="alice", agent_pin=PIN, name="default")
    await client.capture(nb.id, ["The repository builds with make check"], context=OWN)

    await client.observe(nb.id, [draft("the repository builds with make check")], context=OWN)
    await svc.app.state.memory.inbox.drain()

    (fact,) = await client.facts(nb.id)
    assert fact.typed is not None and fact.typed.kind == "project"
    assert len(await client.history(nb.id)) == 1


async def test_many_captures_queued_at_once_land_in_few_locks(svc: MemoryServiceHandle) -> None:
    """Fifty captures into one notebook: all land, each once, in batches (one revision a
    batch, not one a capture)."""
    memory = svc.app.state.memory
    await memory.stop_background()
    client = svc.client()
    nb = await client.ensure(tenant_id="t1", owner="alice", agent_pin=PIN, name="default")

    await asyncio.gather(*(client.observe(nb.id, [draft(f"Service {i:02d} owns a queue")], context=OWN)
                           for i in range(50)))
    await memory.inbox.drain()

    facts = await client.facts(nb.id)
    assert sorted(f.text for f in facts) == sorted(f"Service {i:02d} owns a queue" for i in range(50))
    assert len({r.revision for r in await client.history(nb.id)}) == 1, "one promotion, one lock"


async def test_erasing_an_owner_erases_their_queued_captures(svc: MemoryServiceHandle) -> None:
    memory = svc.app.state.memory
    await memory.stop_background()
    client = svc.client()
    nb = await client.ensure(tenant_id="t1", owner="alice", agent_pin=PIN, name="default")
    await client.observe(nb.id, [draft("Alice's queued fact")], context=OWN)

    await memory.notebooks.store.erase_owner(tenant_id="t1", owner="alice")

    assert await memory.notebooks.store.pending() == 0


async def test_a_memory_service_older_than_the_inbox_takes_plain_captures() -> None:
    """A service without ``/observations`` answers FastAPI's own 404: the client says so
    (None), and capture falls back to the facts' statements."""

    def older(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"detail": "Not Found"})

    client = NotebookClient("http://memory.test", transport=httpx.MockTransport(older))
    try:
        assert await client.observe("t1~abc", [draft("x")], context=OWN) is None
    finally:
        await client.aclose()


async def test_a_contradicted_fact_is_retired_only_once_its_replacement_has_landed(svc: MemoryServiceHandle) -> None:
    """Retirement travels with the capture and goes after it, in one transaction: never
    the old fact gone and the new one still queued (or lost)."""
    memory = svc.app.state.memory
    await memory.stop_background()
    client = svc.client()
    nb = await client.ensure(tenant_id="t1", owner="alice", agent_pin=PIN, name="default")
    await client.capture(nb.id, ["Deploys use flag A"], context=OWN)
    (old,) = await client.facts(nb.id)

    await client.observe(nb.id, [draft("Deploys use flag B")], context=OWN, retire=[old.id])
    assert [f.text for f in await client.facts(nb.id)] == ["Deploys use flag A"], "kept while the new one waits"

    await memory.inbox.drain()
    assert [f.text for f in await client.facts(nb.id)] == ["Deploys use flag B"]


async def test_a_capture_that_does_not_land_retires_nothing(svc: MemoryServiceHandle) -> None:
    memory = svc.app.state.memory
    await memory.stop_background()
    client = svc.client()
    nb = await client.ensure(tenant_id="t1", owner="alice", agent_pin=PIN, name="default")
    await client.capture(nb.id, ["Deploys use flag A"], context=OWN)
    (old,) = await client.facts(nb.id)
    await client.observe(nb.id, [draft("Deploys use flag B")], context=OWN, retire=[old.id])
    await client.set_archived(nb.id, True)  # the queue is dropped: so is its retirement

    await memory.inbox.drain()
    await client.set_archived(nb.id, False)

    assert [f.text for f in await client.facts(nb.id)] == ["Deploys use flag A"]


async def test_a_statement_is_cleaned_as_a_captured_fact_is(svc: MemoryServiceHandle) -> None:
    """A model's "(said in X)" is its claim, not provenance: marked as one, as ``capture``
    marks it."""
    client = svc.client()
    nb = await client.ensure(tenant_id="t1", owner="alice", agent_pin=PIN, name="default")

    await client.observe(nb.id, [draft("- Prefers dark mode (said in #exec-channel)", ObservationType.USER)],
                         context=OWN)

    (fact,) = await landed(client, nb.id, 1)
    assert fact.text == "Prefers dark mode [claimed source: #exec-channel]"


async def test_the_backlog_is_reported(svc: MemoryServiceHandle) -> None:
    memory = svc.app.state.memory
    await memory.stop_background()
    client = svc.client()
    nb = await client.ensure(tenant_id="t1", owner="alice", agent_pin=PIN, name="default")
    await client.observe(nb.id, [draft("One queued")], context=OWN)

    assert (await memory.metrics_snapshot())["inbox_backlog"] == 1
    await memory.inbox.drain()
    assert (await memory.metrics_snapshot())["inbox_backlog"] == 0
