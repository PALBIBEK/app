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


async def hold(memory: object) -> None:
    """Keep captures queued until the test drains them (``memory.inbox.drain``): the drainer
    stopped, and none landed as it is queued."""
    await memory.stop_background()  # type: ignore[attr-defined]
    memory.notebooks.on_queued = lambda: None  # type: ignore[attr-defined]


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
    await hold(memory)  # the archive comes between queue and drain
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
    await hold(memory)
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
    await hold(memory)
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
    await hold(memory)
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
    await hold(memory)
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
    await hold(memory)
    client = svc.client()
    nb = await client.ensure(tenant_id="t1", owner="alice", agent_pin=PIN, name="default")
    await client.observe(nb.id, [draft("One queued")], context=OWN)

    assert (await memory.metrics_snapshot())["inbox_backlog"] == 1
    await memory.inbox.drain()
    assert (await memory.metrics_snapshot())["inbox_backlog"] == 0


async def test_an_older_service_is_probed_once_not_on_every_capture() -> None:
    calls: list[str] = []

    def older(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return httpx.Response(404, json={"detail": "Not Found"})

    client = NotebookClient("http://memory.test", transport=httpx.MockTransport(older))
    try:
        for _ in range(3):
            assert await client.observe("t1~abc", [draft("x")], context=OWN) is None
    finally:
        await client.aclose()

    assert len(calls) == 1


async def test_a_notebook_that_keeps_failing_is_backed_off_and_holds_up_no_other(
    svc: MemoryServiceHandle, monkeypatch: pytest.MonkeyPatch
) -> None:
    memory = svc.app.state.memory
    await hold(memory)
    client = svc.client()
    broken = await client.ensure(tenant_id="t1", owner="alice", agent_pin=PIN, name="broken")
    fine = await client.ensure(tenant_id="t1", owner="alice", agent_pin=PIN, name="fine")
    await client.observe(broken.id, [draft("Never lands")], context=OWN)
    await client.observe(fine.id, [draft("Lands")], context=OWN)
    promote = memory.notebooks.promote

    async def failing(notebook_id: str, **kw: object) -> tuple[int, int]:
        if notebook_id == broken.id:
            raise RuntimeError("an embedder fails on this text")
        return await promote(notebook_id, **kw)

    monkeypatch.setattr(memory.notebooks, "promote", failing)
    assert await memory.inbox.drain() == 1
    assert [f.text for f in await client.facts(fine.id)] == ["Lands"]
    assert broken.id in memory.inbox._backing_off(), "tried again later, not every pass"
    assert await memory.inbox.drain() == 0


async def test_promoted_captures_are_pruned_in_every_notebook(svc: MemoryServiceHandle) -> None:
    from supervisor.memory_service.notebooks.ops import KEEP_PROMOTED_MS
    from supervisor.memory_service.notebooks.store import now_ms

    memory = svc.app.state.memory
    await hold(memory)
    client = svc.client()
    nb = await client.ensure(tenant_id="t1", owner="alice", agent_pin=PIN, name="once")
    await client.observe(nb.id, [draft("Captured once")], context=OWN, idempotency_key="k")
    await memory.inbox.drain()

    assert await memory.notebooks.store.prune_inbox(before=now_ms() + KEEP_PROMOTED_MS) == 1


async def test_a_fact_edited_after_its_retirement_was_queued_is_kept(svc: MemoryServiceHandle) -> None:
    """Resolution judged the fact as it was; one edited since is not that fact."""
    memory = svc.app.state.memory
    await hold(memory)
    client = svc.client()
    nb = await client.ensure(tenant_id="t1", owner="alice", agent_pin=PIN, name="default")
    await client.capture(nb.id, ["Deploys use flag A"], context=OWN)
    (old,) = await client.facts(nb.id)
    await client.observe(nb.id, [draft("Deploys use flag B")], context=OWN, retire=[old.id])
    from supervisor.memory_service.notebooks.types import Edit

    await client.edit(nb.id, [Edit(old.id, "Deploys use flag A, or C on Fridays")], context=OWN)
    await memory.inbox.drain()

    assert sorted(f.text for f in await client.facts(nb.id)) == ["Deploys use flag A, or C on Fridays", "Deploys use flag B"]


async def test_a_full_notebook_refuses_a_capture_up_front(svc: MemoryServiceHandle, monkeypatch: pytest.MonkeyPatch) -> None:
    from supervisor.memory_service.notebooks import ops
    from supervisor.memory_service.notebooks.errors import MemoryLimitExceeded

    monkeypatch.setattr(ops, "MAX_FACTS_PER_NOTEBOOK", 1)
    client = svc.client()
    nb = await client.ensure(tenant_id="t1", owner="alice", agent_pin=PIN, name="default")
    await client.capture(nb.id, ["The only one"], context=OWN)

    with pytest.raises(MemoryLimitExceeded):
        await client.observe(nb.id, [draft("One too many")], context=OWN)


async def test_the_facts_as_they_were_keep_their_type(svc: MemoryServiceHandle) -> None:
    client = svc.client()
    nb = await client.ensure(tenant_id="t1", owner="alice", agent_pin=PIN, name="default")
    await client.observe(nb.id, [draft("The repository builds with make check")], context=OWN)
    (fact,) = await landed(client, nb.id, 1)

    (then,) = await client.facts_at(nb.id, 1)

    assert then.typed == fact.typed


async def test_a_queued_capture_that_cannot_be_read_is_set_aside_not_the_queue_behind_it(svc: MemoryServiceHandle) -> None:
    """A row no reader can parse never will be: set aside at once, and the capture queued
    behind it lands."""
    from supervisor.memory_service.notebooks.types import Provenance

    memory = svc.app.state.memory
    await hold(memory)
    client = svc.client()
    nb = await client.ensure(tenant_id="t1", owner="alice", agent_pin=PIN, name="default")
    store = memory.notebooks.store
    await store.append_inbox(nb.id, [{"type": "bogus"}], Provenance(Sensitivity.ORDINARY, (), False),
                             author=None, expires_on=None, idempotency_key="bad")
    await client.observe(nb.id, [draft("Lands behind it")], context=OWN)

    await memory.inbox.drain()

    assert [f.text for f in await client.facts(nb.id)] == ["Lands behind it"]
    assert await store.pending() == 0


async def test_a_batch_that_fails_to_write_is_landed_one_capture_at_a_time(
    svc: MemoryServiceHandle, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failure while writing rolls the batch back; its captures then land one per
    transaction, so a passing failure costs nothing and a capture that fails on its own
    is counted against it, then set aside, never the queue behind it."""
    from supervisor.memory_service.notebooks.ops import PROMOTE_ATTEMPTS
    from supervisor.memory_service.notebooks.store import FactTx

    memory = svc.app.state.memory
    await hold(memory)
    client = svc.client()
    nb = await client.ensure(tenant_id="t1", owner="alice", agent_pin=PIN, name="default")
    for text in ("First", "Refused by the embedder", "Third"):
        await client.observe(nb.id, [draft(text)], context=OWN)
    insert = FactTx.insert

    async def picky(self: FactTx, text: str, *args: object, **kw: object) -> str:
        if text == "Refused by the embedder":
            raise RuntimeError("the embedder refuses this text")
        return await insert(self, text, *args, **kw)  # type: ignore[arg-type]

    monkeypatch.setattr(FactTx, "insert", picky)
    with pytest.raises(RuntimeError):
        await memory.notebooks.promote(nb.id)
    assert sorted(f.text for f in await client.facts(nb.id)) == ["First", "Third"], "none held up behind it"

    for _ in range(PROMOTE_ATTEMPTS - 1):
        with pytest.raises(RuntimeError):
            await memory.notebooks.promote(nb.id)
    assert await memory.notebooks.store.pending() == 0, "set aside at its last try"


async def test_a_replacement_lands_in_a_full_notebook_and_the_fact_it_replaces_goes(
    svc: MemoryServiceHandle, monkeypatch: pytest.MonkeyPatch
) -> None:
    from supervisor.memory_service.notebooks import ops

    monkeypatch.setattr(ops, "MAX_FACTS_PER_NOTEBOOK", 1)
    client = svc.client()
    nb = await client.ensure(tenant_id="t1", owner="alice", agent_pin=PIN, name="default")
    await client.capture(nb.id, ["Prefers tabs"], context=OWN)
    (old,) = await client.facts(nb.id)

    await client.observe(nb.id, [draft("Prefers spaces", ObservationType.USER)], context=OWN, retire=[old.id])

    async with asyncio.timeout(10):
        while [f.text for f in await client.facts(nb.id)] != ["Prefers spaces"]:
            await asyncio.sleep(0.02)


async def test_editing_a_typed_fact_s_words_takes_its_type_away(svc: MemoryServiceHandle) -> None:
    """A person's words now: the observation's keywords, body and model no longer describe it."""
    from supervisor.memory_service.notebooks.types import Edit

    client = svc.client()
    nb = await client.ensure(tenant_id="t1", owner="alice", agent_pin=PIN, name="default")
    await client.observe(nb.id, [draft("Owns the billing service")], context=OWN)
    (fact,) = await landed(client, nb.id, 1)

    await client.edit(nb.id, [Edit(fact.id, "Owns the payments service")], context=OWN)

    (edited,) = await client.facts(nb.id)
    assert edited.text == "Owns the payments service" and edited.typed is None


async def test_a_statement_cut_to_fit_keeps_its_claim_label_whole(svc: MemoryServiceHandle) -> None:
    from supervisor.memory_service.observations import MAX_STATEMENT_BYTES

    client = svc.client()
    nb = await client.ensure(tenant_id="t1", owner="alice", agent_pin=PIN, name="default")
    raw = "w" * (MAX_STATEMENT_BYTES - 16) + " (said in Slack)"

    await client.observe(nb.id, [draft(raw, ObservationType.USER)], context=OWN)

    (fact,) = await landed(client, nb.id, 1)
    assert fact.text.endswith(" [claimed source: Slack]") and len(fact.text.encode()) <= MAX_STATEMENT_BYTES


async def test_a_retire_id_that_is_not_one_is_refused(svc: MemoryServiceHandle) -> None:
    client = svc.client()
    nb = await client.ensure(tenant_id="t1", owner="alice", agent_pin=PIN, name="default")

    for bad in ("abc", "9" * 30):
        with pytest.raises(InvalidObservation, match="not a fact id"):
            await client.observe(nb.id, [draft("x")], context=OWN, retire=[bad])


async def test_a_fact_the_capture_restates_is_never_the_one_it_retires(svc: MemoryServiceHandle) -> None:
    client = svc.client()
    nb = await client.ensure(tenant_id="t1", owner="alice", agent_pin=PIN, name="default")
    await client.capture(nb.id, ["User prefers tabs"], context=OWN)
    (old,) = await client.facts(nb.id)

    await client.observe(nb.id, [draft("user prefers tabs")], context=OWN, retire=[old.id])
    await svc.app.state.memory.inbox.drain()

    assert [f.text for f in await client.facts(nb.id)] == ["User prefers tabs"]


async def test_a_full_notebook_still_takes_a_repeat_or_a_replacement(
    svc: MemoryServiceHandle, monkeypatch: pytest.MonkeyPatch
) -> None:
    from supervisor.memory_service.notebooks import ops

    monkeypatch.setattr(ops, "MAX_FACTS_PER_NOTEBOOK", 1)
    client = svc.client()
    nb = await client.ensure(tenant_id="t1", owner="alice", agent_pin=PIN, name="default")
    await client.capture(nb.id, ["Deploys use flag A"], context=OWN)
    (old,) = await client.facts(nb.id)

    assert await client.observe(nb.id, [draft("deploys use flag a")], context=OWN) == 1, "a repeat adds nothing"
    assert await client.observe(nb.id, [draft("Deploys use flag B")], context=OWN, retire=[old.id]) == 1


async def test_any_404_that_is_not_the_service_s_own_means_an_older_service() -> None:
    """A proxy's page in front of an older service, not FastAPI's body: still no route
    (a missing notebook is the service's own answer, coded NotebookNotFound)."""

    def proxied(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, text="<html><body>404 Not Found</body></html>")

    client = NotebookClient("http://memory.test", transport=httpx.MockTransport(proxied))
    try:
        assert await client.observe("t1~abc", [draft("x")], context=OWN) is None
    finally:
        await client.aclose()


async def test_clearing_a_notebook_drops_its_queued_captures_too(svc: MemoryServiceHandle) -> None:
    memory = svc.app.state.memory
    await hold(memory)
    client = svc.client()
    nb = await client.ensure(tenant_id="t1", owner="alice", agent_pin=PIN, name="default")
    await client.observe(nb.id, [draft("Queued before the clear")], context=OWN)

    await client.clear(nb.id)
    await memory.inbox.drain()

    assert await client.facts(nb.id) == [], "nothing comes back after a clear"


async def test_a_replacement_that_does_not_all_fit_retires_nothing_and_never_overfills(
    svc: MemoryServiceHandle, monkeypatch: pytest.MonkeyPatch
) -> None:
    """observe refuses it now; one queued anyway (the notebook filled meanwhile) lands
    what fits within the limit and retires nothing."""
    from supervisor.memory_service.notebooks import ops
    from supervisor.memory_service.notebooks.errors import MemoryLimitExceeded
    from supervisor.memory_service.notebooks.types import Provenance
    from supervisor.memory_service.notebooks.wire import observation_out

    monkeypatch.setattr(ops, "MAX_FACTS_PER_NOTEBOOK", 2)
    memory = svc.app.state.memory
    await hold(memory)
    client = svc.client()
    nb = await client.ensure(tenant_id="t1", owner="alice", agent_pin=PIN, name="default")
    await client.capture(nb.id, ["Old fact", "Other fact"], context=OWN)
    old = next(f for f in await client.facts(nb.id) if f.text == "Old fact")

    with pytest.raises(MemoryLimitExceeded):
        await client.observe(nb.id, [draft("New one"), draft("New two")], context=OWN, retire=[old.id])
    await memory.notebooks.store.append_inbox(
        nb.id, [observation_out(draft("New one")), observation_out(draft("New two"))],
        Provenance(Sensitivity.ORDINARY, (), False), author=None, expires_on=None,
        retire=[{"id": old.id, "text": old.text}], idempotency_key=None,
    )
    await memory.inbox.drain()

    facts = sorted(f.text for f in await client.facts(nb.id))
    assert len(facts) <= 2 and "Old fact" in facts, "retired nothing; still within the limit"


async def test_a_database_failure_is_not_counted_against_a_capture(
    svc: MemoryServiceHandle, monkeypatch: pytest.MonkeyPatch
) -> None:
    from sqlalchemy.exc import OperationalError

    from supervisor.memory_service.notebooks.store import FactTx

    memory = svc.app.state.memory
    await hold(memory)
    client = svc.client()
    nb = await client.ensure(tenant_id="t1", owner="alice", agent_pin=PIN, name="default")
    await client.observe(nb.id, [draft("Healthy capture")], context=OWN)
    insert = FactTx.insert
    down = True

    async def flaky(self: FactTx, *args: object, **kw: object) -> str:
        if down:
            raise OperationalError("INSERT", {}, Exception("connection reset"))
        return await insert(self, *args, **kw)  # type: ignore[arg-type]

    monkeypatch.setattr(FactTx, "insert", flaky)
    for _ in range(8):
        with pytest.raises(OperationalError):
            await memory.notebooks.promote(nb.id)
    down = False

    await memory.notebooks.promote(nb.id)
    assert [f.text for f in await client.facts(nb.id)] == ["Healthy capture"], "never set aside for the database's fault"


async def test_restoring_a_typed_fact_to_other_words_takes_its_type_away(svc: MemoryServiceHandle) -> None:
    from supervisor.memory_service.notebooks.types import Edit

    client = svc.client()
    nb = await client.ensure(tenant_id="t1", owner="alice", agent_pin=PIN, name="default")
    await client.capture(nb.id, ["Builds with make"], context=OWN)
    (fact,) = await client.facts(nb.id)
    await client.edit(nb.id, [Edit(fact.id, "Builds with make check")], context=OWN)
    await client.observe(nb.id, [draft("builds with make check")], context=OWN)  # gives the fact a type
    await svc.app.state.memory.inbox.drain()
    assert (await client.facts(nb.id))[0].typed is not None

    await client.restore(nb.id, 1)

    (restored,) = await client.facts(nb.id)
    assert restored.text == "Builds with make" and restored.typed is None


@pytest.mark.parametrize("transient", ["pool", "connection"])
async def test_the_pool_or_the_connection_failing_is_never_counted_against_a_capture(
    svc: MemoryServiceHandle, monkeypatch: pytest.MonkeyPatch, transient: str
) -> None:
    from sqlalchemy.exc import InterfaceError
    from sqlalchemy.exc import TimeoutError as PoolTimeout

    from supervisor.memory_service.notebooks.ops import PROMOTE_ATTEMPTS
    from supervisor.memory_service.notebooks.store import FactTx

    memory = svc.app.state.memory
    await hold(memory)
    client = svc.client()
    nb = await client.ensure(tenant_id="t1", owner="alice", agent_pin=PIN, name="default")
    await client.observe(nb.id, [draft("Healthy capture")], context=OWN)
    error = PoolTimeout("QueuePool limit reached") if transient == "pool" else InterfaceError("x", {}, Exception("gone"))
    insert, down = FactTx.insert, [True]

    async def flaky(self: FactTx, *args: object, **kw: object) -> str:
        if down[0]:
            raise error
        return await insert(self, *args, **kw)  # type: ignore[arg-type]

    monkeypatch.setattr(FactTx, "insert", flaky)
    for _ in range(PROMOTE_ATTEMPTS + 2):
        with pytest.raises(type(error)):
            await memory.notebooks.promote(nb.id)
    down[0] = False

    await memory.notebooks.promote(nb.id)
    assert [f.text for f in await client.facts(nb.id)] == ["Healthy capture"]


async def test_a_constraint_error_landing_a_capture_is_its_own_and_sets_it_aside(
    svc: MemoryServiceHandle, monkeypatch: pytest.MonkeyPatch
) -> None:
    from sqlalchemy.exc import IntegrityError

    from supervisor.memory_service.notebooks.ops import PROMOTE_ATTEMPTS
    from supervisor.memory_service.notebooks.store import FactTx

    memory = svc.app.state.memory
    await hold(memory)
    client = svc.client()
    nb = await client.ensure(tenant_id="t1", owner="alice", agent_pin=PIN, name="default")
    await client.observe(nb.id, [draft("Violates a constraint")], context=OWN)
    await client.observe(nb.id, [draft("Behind it")], context=OWN)
    insert = FactTx.insert

    async def strict(self: FactTx, text: str, *args: object, **kw: object) -> str:
        if text == "Violates a constraint":
            raise IntegrityError("INSERT", {}, Exception("check constraint"))
        return await insert(self, text, *args, **kw)  # type: ignore[arg-type]

    monkeypatch.setattr(FactTx, "insert", strict)
    for _ in range(PROMOTE_ATTEMPTS):
        with pytest.raises(IntegrityError):
            await memory.notebooks.promote(nb.id)
    await memory.notebooks.promote(nb.id)

    assert [f.text for f in await client.facts(nb.id)] == ["Behind it"]


async def test_a_retried_burst_after_a_clear_brings_nothing_back(svc: MemoryServiceHandle) -> None:
    memory = svc.app.state.memory
    await hold(memory)
    client = svc.client()
    nb = await client.ensure(tenant_id="t1", owner="alice", agent_pin=PIN, name="default")
    await client.observe(nb.id, [draft("Cleared away")], context=OWN, idempotency_key="burst-9")
    await client.clear(nb.id)

    assert await client.observe(nb.id, [draft("Cleared away")], context=OWN, idempotency_key="burst-9") == 0
    await memory.inbox.drain()
    assert await client.facts(nb.id) == []


async def test_a_capture_whose_every_statement_is_empty_is_refused(svc: MemoryServiceHandle) -> None:
    client = svc.client()
    nb = await client.ensure(tenant_id="t1", owner="alice", agent_pin=PIN, name="default")

    with pytest.raises(InvalidObservation, match="empty"):
        await client.observe(nb.id, [draft("-")], context=OWN, retire=[])


async def test_captures_queued_count_toward_a_near_full_notebook(
    svc: MemoryServiceHandle, monkeypatch: pytest.MonkeyPatch
) -> None:
    from supervisor.memory_service.notebooks import ops
    from supervisor.memory_service.notebooks.errors import MemoryLimitExceeded

    monkeypatch.setattr(ops, "MAX_FACTS_PER_NOTEBOOK", 3)
    memory = svc.app.state.memory
    await hold(memory)
    client = svc.client()
    nb = await client.ensure(tenant_id="t1", owner="alice", agent_pin=PIN, name="default")
    await client.capture(nb.id, ["Already here"], context=OWN)

    assert await client.observe(nb.id, [draft("One"), draft("Two")], context=OWN) == 2
    with pytest.raises(MemoryLimitExceeded):
        await client.observe(nb.id, [draft("Three")], context=OWN)  # one count, queued captures included


async def test_a_retirement_finds_a_fact_that_shares_its_text_with_another(svc: MemoryServiceHandle) -> None:
    from supervisor.memory_service.notebooks.types import Edit

    memory = svc.app.state.memory
    await hold(memory)
    client = svc.client()
    nb = await client.ensure(tenant_id="t1", owner="alice", agent_pin=PIN, name="default")
    await client.capture(nb.id, ["Uses tabs", "Uses spaces"], context=OWN)
    tabs, spaces = sorted(await client.facts(nb.id), key=lambda f: f.text, reverse=True)
    await client.edit(nb.id, [Edit(spaces.id, "uses tabs")], context=OWN)  # two facts, one normalized text
    await client.observe(nb.id, [draft("Uses neither")], context=OWN, retire=[spaces.id])

    await memory.inbox.drain()

    assert sorted(f.id for f in await client.facts(nb.id) if f.id in (tabs.id, spaces.id)) == [tabs.id]


def test_postgres_contention_reported_as_a_plain_dbapi_error_is_transient() -> None:
    """asyncpg's deadlocks and timeouts reach SQLAlchemy as a plain DBAPIError: told by
    their SQLSTATE, never counted against a capture."""
    from sqlalchemy.exc import DataError, DBAPIError

    from supervisor.memory_service.notebooks.ops import is_transient

    class Orig(Exception):
        def __init__(self, sqlstate: str) -> None:
            super().__init__(sqlstate)
            self.sqlstate = sqlstate

    for state in ("40P01", "40001", "57014", "55P03", "08006"):
        assert is_transient(DBAPIError("x", {}, Orig(state))), state
    assert not is_transient(DBAPIError("x", {}, Orig("23505"))), "a unique violation is the capture's"
    assert not is_transient(DataError("x", {}, Orig("22001")))
    assert not is_transient(ValueError("bad"))


async def test_keywords_sent_as_a_string_are_refused_not_split(svc: MemoryServiceHandle) -> None:
    from supervisor.memory_service.notebooks.wire import observation_out

    client = svc.client()
    nb = await client.ensure(tenant_id="t1", owner="alice", agent_pin=PIN, name="default")
    body = observation_out(draft("Deploys on Fridays")) | {"keywords": "deploy"}

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=svc.app), base_url="http://memory.test") as raw:
        from supervisor.seams.services.memory_service_client import memory_service_credential

        r = await raw.post(f"/v1/notebooks/{nb.id}/observations", json={"observations": [body]},
                           headers={"Authorization": f"Bearer {memory_service_credential('t1')}"})
    assert r.status_code == 422 and r.json()["detail"]["code"] == "InvalidObservation"


async def test_the_limit_counts_facts_two_of_one_text_both(
    svc: MemoryServiceHandle, monkeypatch: pytest.MonkeyPatch
) -> None:
    from supervisor.memory_service.notebooks import ops
    from supervisor.memory_service.notebooks.store import FactTx
    from supervisor.memory_service.notebooks.types import Edit, Provenance

    client = svc.client()
    nb = await client.ensure(tenant_id="t1", owner="alice", agent_pin=PIN, name="default")
    await client.capture(nb.id, ["Uses tabs", "Uses spaces"], context=OWN)
    spaces = next(f for f in await client.facts(nb.id) if f.text == "Uses spaces")
    await client.edit(nb.id, [Edit(spaces.id, "uses tabs")], context=OWN)  # two live facts, one text
    monkeypatch.setattr(ops, "MAX_FACTS_PER_NOTEBOOK", 2)

    async def run(tx: FactTx) -> tuple[int, bool]:
        return await ops.land(tx, [("A third", None)], Provenance(Sensitivity.ORDINARY, (), False), None,
                              over_limit="drop")

    added, whole = await svc.app.state.memory.notebooks.store.transact(nb.id, run)
    assert (added, whole) == (0, False), "full at two facts, though they share one text"


async def test_a_fault_every_capture_meets_is_not_counted_against_them(
    svc: MemoryServiceHandle, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every capture failing on its own means the fault is not theirs (a bug, the bank down
    for all): none is counted, so none is set aside and lost to it."""
    from supervisor.memory_service.notebooks.ops import PROMOTE_ATTEMPTS
    from supervisor.memory_service.notebooks.store import FactTx

    memory = svc.app.state.memory
    await hold(memory)
    client = svc.client()
    nb = await client.ensure(tenant_id="t1", owner="alice", agent_pin=PIN, name="default")
    for text in ("One", "Two"):
        await client.observe(nb.id, [draft(text)], context=OWN)
    insert, broken = FactTx.insert, [True]

    async def bug(self: FactTx, *args: object, **kw: object) -> str:
        if broken[0]:
            raise ValueError("a bug in the bank's index")
        return await insert(self, *args, **kw)  # type: ignore[arg-type]

    monkeypatch.setattr(FactTx, "insert", bug)
    for _ in range(PROMOTE_ATTEMPTS + 1):
        with pytest.raises(ValueError):
            await memory.notebooks.promote(nb.id)
    broken[0] = False

    await memory.notebooks.promote(nb.id)
    assert sorted(f.text for f in await client.facts(nb.id)) == ["One", "Two"]


async def test_a_failing_ahead_embedding_never_stops_the_landing(
    svc: MemoryServiceHandle, monkeypatch: pytest.MonkeyPatch
) -> None:
    memory = svc.app.state.memory
    await hold(memory)
    client = svc.client()
    nb = await client.ensure(tenant_id="t1", owner="alice", agent_pin=PIN, name="default")
    await client.observe(nb.id, [draft("Lands anyway")], context=OWN)

    async def refused(*args: object, **kw: object) -> None:
        raise RuntimeError("the embedder refuses the batch")

    monkeypatch.setattr(memory.notebooks.store, "embed_queued", refused)
    await memory.notebooks.promote(nb.id)

    assert [f.text for f in await client.facts(nb.id)] == ["Lands anyway"]


async def test_a_drain_goes_past_notebooks_that_land_nothing_to_the_next(
    svc: MemoryServiceHandle, monkeypatch: pytest.MonkeyPatch
) -> None:
    from supervisor.memory_service.notebooks import inbox

    monkeypatch.setattr(inbox, "NOTEBOOKS_PER_PASS", 1)
    memory = svc.app.state.memory
    await hold(memory)
    client = svc.client()
    first = await client.ensure(tenant_id="t1", owner="alice", agent_pin=PIN, name="first")
    second = await client.ensure(tenant_id="t1", owner="alice", agent_pin=PIN, name="second")
    await client.observe(first.id, [draft("Stuck")], context=OWN)
    await client.observe(second.id, [draft("Next in line")], context=OWN)
    promote = memory.notebooks.promote

    async def first_taken_elsewhere(notebook_id: str, **kw: object) -> tuple[int, int]:
        if notebook_id == first.id:
            return 0, 0  # another replica took its turn
        return await promote(notebook_id, **kw)

    monkeypatch.setattr(memory.notebooks, "promote", first_taken_elsewhere)
    assert await memory.inbox.drain() == 1
    assert [f.text for f in await client.facts(second.id)] == ["Next in line"]


def test_the_app_wrapper_copies_without_recursing() -> None:
    import copy

    from fastapi import FastAPI

    from supervisor.main import WithRequestThreads

    wrapped = WithRequestThreads(FastAPI())

    assert copy.copy(wrapped).app is wrapped.app
