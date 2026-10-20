"""Disclosure: what each audience may see and edit; classification of captures."""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from supervisor.memory.classification import ClassifiedMemory
from supervisor.memory.disclosure import Audience, DisclosedMemory
from supervisor.memory.service import CaptureContext, NotebookMemory
from supervisor.memory.store import NotebookStore
from supervisor.memory_service.notebooks.errors import MemoryDisclosureDenied, MemoryFactsMissing
from supervisor.memory_service.notebooks.types import (
    Edit,
    Provenance,
    Sensitivity,
    personal_source,
    scope_source,
)

pytestmark = pytest.mark.asyncio

Factory = async_sessionmaker[AsyncSession]
ALICE, BOB = "alice", "bob"
X = scope_source("x")


class Members:
    """Scope membership that tests can change between reads (and make fail)."""

    def __init__(self, **scopes: set[str]) -> None:
        self.scopes = scopes
        self.broken = False

    async def __call__(self, principal: str, scope_id: str) -> bool:
        if self.broken:
            raise ConnectionError("membership service down")
        return principal in self.scopes.get(scope_id, set())


async def _seeded(memory_db: Factory) -> tuple[NotebookMemory, str]:
    store = NotebookStore(memory_db)
    nb = (await store.ensure(tenant_id="t1", owner=ALICE, agent_pin="p", name="default")).id
    memory = NotebookMemory(store)

    async def put(text: str, provenance: Provenance) -> None:
        await memory.capture(
            nb,
            [text],
            context=CaptureContext(
                sensitivity=provenance.sensitivity,
                source=provenance.sources[0] if provenance.sources else None,
                inherited=None if provenance.source_unknown else (),
            ),
        )

    await put("ordinary personal", Provenance(Sensitivity.ORDINARY, (personal_source(ALICE),)))
    await put("sensitive personal", Provenance(Sensitivity.SENSITIVE, (personal_source(ALICE),)))
    await put("learned in x", Provenance(Sensitivity.SENSITIVE, (X,)))
    await put("untracked", Provenance(Sensitivity.UNKNOWN, (), True))
    return memory, nb


def _view(memory: NotebookMemory, audience: Audience) -> DisclosedMemory:
    async def home_of(notebook_id: str) -> str:
        return (await memory.store.get(notebook_id)).home

    return DisclosedMemory(memory, audience, home_of)


async def _texts(view: DisclosedMemory, nb: str) -> set[str]:
    return {f.text for f in await view.facts(nb)}


async def test_a_private_session_sees_its_owners_notebook(memory_db: Factory) -> None:
    memory, nb = await _seeded(memory_db)
    members = Members(x={ALICE})
    view = _view(memory, Audience(ALICE, personal_source(ALICE), (ALICE,), members))
    assert await _texts(view, nb) == {
        "ordinary personal",
        "sensitive personal",
        "learned in x",
        "untracked",
    }
    # Leaving scope x withdraws what was learned there, on the very next read.
    members.scopes["x"] = set()
    assert "learned in x" not in await _texts(view, nb)


async def test_a_shared_audience_sees_only_what_everyone_is_entitled_to(memory_db: Factory) -> None:
    memory, nb = await _seeded(memory_db)
    members = Members(x={ALICE, BOB})
    shared = Audience(ALICE, X, (ALICE, BOB), members)
    assert await _texts(_view(memory, shared), nb) == {"learned in x"}
    # QM's open posture lets only *ordinary* personal facts follow their owner in.
    opened = Audience(ALICE, X, (ALICE, BOB), members, open=True)
    assert await _texts(_view(memory, opened), nb) == {"learned in x", "ordinary personal"}
    external = Audience(ALICE, X, (ALICE, BOB), members, open=True, internal=lambda p: p != BOB)
    assert await _texts(_view(memory, external), nb) == {"learned in x"}


async def test_lookup_failures_fail_closed(memory_db: Factory) -> None:
    memory, nb = await _seeded(memory_db)
    members = Members(x={ALICE})
    members.broken = True
    view = _view(memory, Audience(ALICE, personal_source(ALICE), (ALICE,), members))
    assert "learned in x" not in await _texts(view, nb)
    assert "ordinary personal" in await _texts(view, nb), "no lookup needed for own facts"


async def test_recall_search_and_read_use_the_same_view(memory_db: Factory) -> None:
    memory, nb = await _seeded(memory_db)
    shared = _view(memory, Audience(ALICE, X, (ALICE, BOB), Members(x={ALICE, BOB})))
    assert [c.text.split(") ", 1)[1] for c in await shared.recall(nb)] == ["learned in x"]
    assert await shared.query(nb, "personal") == []
    assert "personal" not in await shared.read(nb)


async def test_only_a_narrow_audience_may_edit_and_never_hidden_facts(memory_db: Factory) -> None:
    memory, nb = await _seeded(memory_db)
    members = Members(x={ALICE, BOB})
    facts = {f.text: f for f in await memory.facts(nb)}
    shared = _view(memory, Audience(ALICE, X, (ALICE, BOB), members))
    with pytest.raises(MemoryDisclosureDenied):
        await shared.edit(nb, [Edit(facts["learned in x"].id, "changed")])
    members.scopes["x"] = {ALICE}
    private = _view(memory, Audience(ALICE, personal_source(ALICE), (ALICE,), members))
    await private.edit(nb, [Edit(facts["learned in x"].id, "changed")])
    members.scopes["x"] = set()  # now hidden from alice too
    with pytest.raises(MemoryFactsMissing):
        await private.delete(nb, [facts["learned in x"].id])
    # A partially visible notebook cannot be cleared or restored.
    with pytest.raises(MemoryDisclosureDenied):
        await private.clear(nb)
    with pytest.raises(MemoryDisclosureDenied):
        await private.restore(nb, 1)
    assert len(await memory.facts(nb)) == 4, "nothing was changed"


async def test_explicit_captures_and_edits_are_classified(memory_db: Factory) -> None:
    store = NotebookStore(memory_db)
    nb = (await store.ensure(tenant_id="t1", owner=ALICE, agent_pin="p", name="default")).id
    asked: list[str] = []

    async def model(system: str, content: str) -> str:
        asked.append(content)
        return "sensitive" if "salary" in content.lower() else "ordinary"

    memory = ClassifiedMemory(NotebookMemory(store), model)
    await memory.capture(nb, ["Prefers tea"], context=CaptureContext(inherited=()))
    await memory.capture(nb, ["Salary is 100k"], context=CaptureContext(inherited=()))
    # A caller's own label is a floor the model cannot lower.
    await memory.capture(
        nb, ["Likes jazz"], context=CaptureContext(sensitivity=Sensitivity.RESTRICTED, inherited=())
    )
    by_text = {f.text: f.provenance.sensitivity for f in await memory.facts(nb)}
    assert by_text == {
        "Prefers tea": Sensitivity.ORDINARY,
        "Salary is 100k": Sensitivity.SENSITIVE,
        "Likes jazz": Sensitivity.RESTRICTED,
    }
    # Automatic captures arrive labelled by extraction and are not classified again.
    calls = len(asked)
    await memory.capture(
        nb,
        ["Owns billing"],
        context=CaptureContext(mode="automatic", sensitivity=Sensitivity.ORDINARY, inherited=()),
    )
    assert len(asked) == calls
    tea = next(f for f in await memory.facts(nb) if f.text == "Prefers tea")
    await memory.edit(nb, [Edit(tea.id, "Salary talk: prefers tea")])
    edited = next(f for f in await memory.facts(nb) if f.id == tea.id)
    assert edited.provenance.sensitivity is Sensitivity.SENSITIVE


async def test_a_failing_classifier_means_unknown(memory_db: Factory) -> None:
    store = NotebookStore(memory_db)
    nb = (await store.ensure(tenant_id="t1", owner=ALICE, agent_pin="p", name="default")).id

    async def broken(system: str, content: str) -> str:
        raise TimeoutError

    async def noisy(system: str, content: str) -> str:
        return "Label: totally fine!"

    for model, text in ((broken, "a"), (noisy, "b")):
        await ClassifiedMemory(NotebookMemory(store), model).capture(
            nb, [text], context=CaptureContext(inherited=())
        )
    assert {f.provenance.sensitivity for f in await NotebookMemory(store).facts(nb)} == {
        Sensitivity.UNKNOWN
    }
