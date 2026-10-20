"""Notebook memory: capture, provenance, edits, history and restore (QM semantics)."""

from __future__ import annotations

import asyncio

import pytest

from supervisor.memory.client import NotebookClient
from supervisor.memory.service import CaptureContext, NotebookMemory
from supervisor.memory_service.notebooks.errors import (
    MemoryFactsMissing,
    NotebookExists,
    RevisionConflict,
)
from supervisor.memory_service.notebooks.render import clean_captured, fact_line
from supervisor.memory_service.notebooks.types import (
    CaptureMetadata,
    Edit,
    Provenance,
    Sensitivity,
    personal_source,
    scope_source,
)
from tests.support.notebooks import MemoryServiceHandle

pytestmark = pytest.mark.asyncio

ALICE = "alice"
PIN = "composition:githi@sha256:abc"
SCOPE_X = scope_source("x")
ORDINARY_TRACKED = CaptureContext(sensitivity=Sensitivity.ORDINARY, inherited=())


async def _memory(notebooks: NotebookClient) -> tuple[NotebookMemory, str]:
    store = notebooks
    notebook = await store.ensure(tenant_id="t1", owner=ALICE, agent_pin=PIN, name="default")
    return NotebookMemory(store), notebook.id


def test_captured_text_is_cleaned_and_claims_are_labelled() -> None:
    assert clean_captured(
        [
            "-  Prefers   terse replies ",
            "(2024-01-02) Shipped v2",
            "Owns billing (said in #ops)",
            " ",
        ]
    ) == [
        "Prefers terse replies",
        "on 2024-01-02: Shipped v2",
        "Owns billing [claimed source: #ops]",
    ]


async def test_capture_dedupes_and_tightens_provenance(notebooks: NotebookClient) -> None:
    memory, nb = await _memory(notebooks)
    assert await memory.capture(nb, ["Prefers tea", "Owns billing"], context=ORDINARY_TRACKED) == 2
    # The same fact again (case and spacing differ) adds no text...
    sensitive = CaptureContext(sensitivity=Sensitivity.SENSITIVE, source=SCOPE_X, inherited=())
    assert await memory.capture(nb, ["  prefers TEA "], context=sensitive) == 0
    facts = {f.text: f for f in await memory.facts(nb)}
    assert set(facts) == {"Prefers tea", "Owns billing"}
    # ...but what we know about it only tightens: now sensitive, and seen in scope X too.
    tea = facts["Prefers tea"].provenance
    assert tea.sensitivity is Sensitivity.SENSITIVE
    assert set(tea.sources) == {personal_source(ALICE), SCOPE_X}


async def test_untracked_context_makes_provenance_unknown(notebooks: NotebookClient) -> None:
    memory, nb = await _memory(notebooks)
    await memory.capture(
        nb, ["Likes jazz"], context=CaptureContext(sensitivity=Sensitivity.ORDINARY)
    )
    (fact,) = await memory.facts(nb)
    assert fact.provenance.source_unknown, "inherited=None means untracked, not clean"
    inherited = (Provenance(Sensitivity.RESTRICTED, (SCOPE_X,), False),)
    await memory.capture(nb, ["Has a launch code"], context=CaptureContext(inherited=inherited))
    secret = next(f for f in await memory.facts(nb) if "launch" in f.text)
    assert secret.provenance.sensitivity is Sensitivity.RESTRICTED  # inherited, not lowered
    assert SCOPE_X in secret.provenance.sources and not secret.provenance.source_unknown


async def test_edits_keep_ids_and_are_all_or_nothing(notebooks: NotebookClient) -> None:
    memory, nb = await _memory(notebooks)
    await memory.capture(nb, ["Prefers tea", "Owns billing"], context=ORDINARY_TRACKED)
    tea, billing = await memory.facts(nb)
    with pytest.raises(MemoryFactsMissing) as err:
        await memory.edit(nb, [Edit(tea.id, "Prefers green tea"), Edit("999", "nope")])
    assert err.value.ids == ("999",)
    assert [f.text for f in await memory.facts(nb)] == ["Prefers tea", "Owns billing"]
    await memory.edit(nb, [Edit(f"#{tea.id}", "- Prefers green tea")])
    first = (await memory.facts(nb))[0]
    assert (first.id, first.text) == (tea.id, "Prefers green tea")
    # Editing from another conversation exposes the fact to that conversation's source.
    await memory.edit(
        nb, [Edit(billing.id, "Owns billing and payroll")], context=CaptureMetadata(source=SCOPE_X)
    )
    edited = (await memory.facts(nb))[1]
    assert SCOPE_X in edited.provenance.sources and edited.provenance.source_unknown


async def test_every_change_is_one_revision_and_clearing_keeps_history(notebooks: NotebookClient) -> None:
    memory, nb = await _memory(notebooks)
    store = memory.store
    await memory.capture(nb, ["a", "b"], context=ORDINARY_TRACKED)
    assert (await store.get(nb)).revision == 1, "one transaction, one revision"
    await memory.capture(nb, ["a"], context=ORDINARY_TRACKED)
    assert (await store.get(nb)).revision == 1, "a no-op capture is not a revision"
    _, b = await memory.facts(nb)
    await memory.delete(nb, [b.id])
    assert [f.text for f in await memory.facts(nb)] == ["a"]
    assert await memory.clear(nb) == 1
    assert await memory.facts(nb) == []
    ops = [(r.revision, r.op, r.text) for r in await memory.history(nb)]
    assert ops == [(1, "insert", "a"), (1, "insert", "b"), (2, "delete", "b"), (3, "delete", "a")]


async def test_history_reads_and_restores_never_declassify(notebooks: NotebookClient) -> None:
    memory, nb = await _memory(notebooks)
    await memory.capture(nb, ["Codename is Kiwi", "Prefers tea"], context=ORDINARY_TRACKED)
    kiwi, tea = await memory.facts(nb)
    # Later the codename is learned to be restricted, then reworded; tea is deleted.
    await memory.capture(
        nb,
        ["codename is kiwi"],
        context=CaptureContext(sensitivity=Sensitivity.RESTRICTED, inherited=()),
    )
    await memory.edit(nb, [Edit(kiwi.id, "Codename is Banana")])
    await memory.delete(nb, [tea.id])
    await memory.capture(nb, ["Added later"], context=ORDINARY_TRACKED)

    historic = {f.text: f for f in await memory.facts_at(nb, 1)}
    assert set(historic) == {"Codename is Kiwi", "Prefers tea"}
    assert historic["Codename is Kiwi"].provenance.sensitivity is Sensitivity.RESTRICTED

    await memory.restore(nb, 1)
    restored = {f.text: f for f in await memory.facts(nb)}
    assert set(restored) == {"Codename is Kiwi", "Prefers tea"}, "later facts removed"
    assert restored["Prefers tea"].id == tea.id, "a deleted fact comes back under its id"
    assert restored["Codename is Kiwi"].provenance.sensitivity is Sensitivity.RESTRICTED
    assert (await memory.history(nb))[-1].op in {"restore", "update", "delete"}


async def test_concurrent_writers_never_lose_a_fact(memory_svc: MemoryServiceHandle) -> None:
    memory, nb = await _memory(memory_svc.client())
    others = [NotebookMemory(memory_svc.client()) for _ in range(4)]  # other workers
    writers = [memory, *others]
    await asyncio.gather(
        *(
            writers[i % len(writers)].capture(nb, [f"fact {i}"], context=ORDINARY_TRACKED)
            for i in range(20)
        )
    )
    facts = await memory.facts(nb)
    assert sorted(f.text for f in facts) == sorted(f"fact {i}" for i in range(20))
    revisions = [r.revision for r in await memory.history(nb)]
    assert sorted(set(revisions)) == list(range(1, 21)), "each writer got its own revision"


async def test_stale_writes_are_refused(memory_svc: MemoryServiceHandle) -> None:
    """The store's stale-write check, where the transactions run (the memory service)."""
    memory, nb = await _memory(memory_svc.client())
    await memory.capture(nb, ["a"], context=ORDINARY_TRACKED)

    async def noop(_tx: object) -> None:
        return None

    store = memory_svc.app.state.memory.notebooks.store
    with pytest.raises(RevisionConflict):
        await store.transact(nb, noop, expect_revision=0)


async def test_notebook_names_are_unique_per_owner_and_pin(notebooks: NotebookClient) -> None:
    store = notebooks
    made = await asyncio.gather(
        *(
            store.ensure(tenant_id="t1", owner=ALICE, agent_pin=PIN, name="project")
            for _ in range(5)
        )
    )
    assert len({n.id for n in made}) == 1
    with pytest.raises(NotebookExists):
        await store.create(tenant_id="t1", owner=ALICE, agent_pin=PIN, name="project")
    other_pin = await store.ensure(
        tenant_id="t1", owner=ALICE, agent_pin="harness:v2@x", name="project"
    )
    other_owner = await store.ensure(tenant_id="t1", owner="bob", agent_pin=PIN, name="project")
    assert len({made[0].id, other_pin.id, other_owner.id}) == 3


async def test_fact_lines_carry_ids_for_editing(notebooks: NotebookClient) -> None:
    memory, nb = await _memory(notebooks)
    await memory.capture(nb, ["Prefers tea"], context=ORDINARY_TRACKED)
    (fact,) = await memory.facts(nb)
    assert fact_line(fact).startswith(f"#{fact.id} (")
    assert (await memory.query(nb, "TEA prefers"))[0] == fact_line(fact)
    assert await memory.query(nb, "coffee") == []
