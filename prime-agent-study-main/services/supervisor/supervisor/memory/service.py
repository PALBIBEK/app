"""The memory service: what recall, tools, capture and the API call (QM's MemoryService).

``NotebookMemory`` is the built-in implementation over ``NotebookStore``. Views
(disclosure, classification, semantic ranking, provider routing) wrap a service
and return another, so callers never see which layers are present.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import Literal, Protocol, runtime_checkable

from supervisor.memory.store import FactTx, NotebookStore
from supervisor.memory_service.notebooks.errors import MemoryFactsMissing
from supervisor.memory_service.notebooks.render import (
    clean_captured,
    edited_text,
    normalize,
    parse_fact_id,
    query_facts,
    recent_candidates,
    render_facts,
)
from supervisor.memory_service.notebooks.types import (
    Candidate,
    CaptureMetadata,
    Edit,
    Fact,
    Provenance,
    RecallContext,
    Revision,
    capture_provenance,
    merge_provenance,
)

type CaptureMode = Literal["explicit", "automatic"]


@dataclass(frozen=True, slots=True)
class CaptureContext(CaptureMetadata):
    """A capture's runtime context: how it was triggered, by whom, from what exchange."""

    mode: CaptureMode = "explicit"
    actor: str | None = None
    input: str | None = None
    reply: str | None = None
    idempotency_key: str | None = None
    autonomous: bool = False


@runtime_checkable
class MemoryService(Protocol):
    """One notebook-addressed memory. ``manages`` services support edits and history."""

    manages: bool

    async def recall(
        self, notebook_id: str, context: RecallContext | None = None
    ) -> list[Candidate]: ...

    async def capture(
        self,
        notebook_id: str,
        facts: Sequence[str],
        *,
        author: str | None = None,
        context: CaptureContext | None = None,
    ) -> int: ...

    async def query(
        self, notebook_id: str, q: str, limit: int = 20, context: RecallContext | None = None
    ) -> list[str]: ...

    async def read(self, notebook_id: str) -> str: ...

    async def facts(self, notebook_id: str) -> list[Fact]: ...

    async def edit(
        self,
        notebook_id: str,
        edits: Sequence[Edit],
        *,
        author: str | None = None,
        context: CaptureMetadata | None = None,
    ) -> None: ...

    async def delete(
        self, notebook_id: str, ids: Sequence[str], *, author: str | None = None
    ) -> None: ...

    async def clear(self, notebook_id: str, *, author: str | None = None) -> int: ...

    async def history(self, notebook_id: str) -> list[Revision]: ...

    async def facts_at(self, notebook_id: str, revision: int) -> list[Fact]: ...

    async def restore(
        self, notebook_id: str, revision: int, *, author: str | None = None
    ) -> None: ...


def resolve_ids(facts: Sequence[Fact], ids: Sequence[str]) -> list[Fact]:
    """The facts ``ids`` name, in order; any unknown id fails the whole operation."""
    by_id = {f.id: f for f in facts}
    wanted = [parse_fact_id(i) for i in ids]
    if missing := list(dict.fromkeys(i for i in wanted if i not in by_id)):
        raise MemoryFactsMissing(missing)
    return [by_id[i] for i in wanted]


def state_at(revisions: Sequence[Revision], revision: int) -> dict[str, Fact]:
    """Facts live at ``revision``, each floored by its later history (never declassified).

    A restored or historically read fact keeps the strictest provenance it ever had
    after that point: a later revision may have learned it was sensitive.
    """
    latest: dict[str, Revision] = {}
    created: dict[str, int] = {}
    later: defaultdict[str, list[Provenance]] = defaultdict(list)
    for rev in revisions:
        created.setdefault(rev.fact_id, rev.at)
        if rev.revision <= revision:
            latest[rev.fact_id] = rev
        else:
            later[rev.fact_id].append(rev.provenance)
    out: dict[str, Fact] = {}
    for fact_id, rev in latest.items():
        if rev.op == "delete":
            continue
        floor = merge_provenance([rev.provenance, *later[fact_id]], rev.provenance.sensitivity)
        out[fact_id] = Fact(fact_id, rev.text, floor, created[fact_id], rev.at, rev.author)
    return out


class NotebookMemory:
    """Built-in notebook memory: stable-id facts, deduplicated capture, full history."""

    manages = True

    def __init__(self, store: NotebookStore) -> None:
        self._store = store

    @property
    def store(self) -> NotebookStore:
        return self._store

    async def recall(
        self, notebook_id: str, context: RecallContext | None = None
    ) -> list[Candidate]:
        return recent_candidates(await self._store.facts(notebook_id))

    async def capture(
        self,
        notebook_id: str,
        facts: Sequence[str],
        *,
        author: str | None = None,
        context: CaptureContext | None = None,
    ) -> int:
        lines = clean_captured(facts)
        if not lines:
            return 0
        notebook = await self._store.get(notebook_id)
        provenance = capture_provenance(notebook.home, context)

        async def run(tx: FactTx) -> int:
            existing = {normalize(f.text): f for f in await tx.current()}
            added = 0
            for text in lines:
                key = normalize(text)
                if (duplicate := existing.get(key)) is not None:
                    # A repeat adds no text, but may tighten what we know about it.
                    merged = merge_provenance(
                        [duplicate.provenance, provenance], duplicate.provenance.sensitivity
                    )
                    if not merged.same_as(duplicate.provenance):
                        await tx.update(duplicate.id, duplicate.text, merged)
                        existing[key] = replace(duplicate, provenance=merged)
                    continue
                fact_id = await tx.insert(text, provenance)
                existing[key] = Fact(fact_id, text, provenance, tx.at, tx.at, author)
                added += 1
            return added

        return await self._store.transact(notebook_id, run, author=author)

    async def query(
        self, notebook_id: str, q: str, limit: int = 20, context: RecallContext | None = None
    ) -> list[str]:
        return query_facts(await self._store.facts(notebook_id), q, limit)

    async def read(self, notebook_id: str) -> str:
        return render_facts(await self._store.facts(notebook_id))

    async def facts(self, notebook_id: str) -> list[Fact]:
        return await self._store.facts(notebook_id)

    async def edit(
        self,
        notebook_id: str,
        edits: Sequence[Edit],
        *,
        author: str | None = None,
        context: CaptureMetadata | None = None,
    ) -> None:
        notebook = await self._store.get(notebook_id)
        # Editing from a conversation exposes the fact to that conversation's sources.
        exposure = capture_provenance(notebook.home, context) if context else None

        async def run(tx: FactTx) -> None:
            targets = resolve_ids(await tx.current(), [e.id for e in edits])
            for edit, fact in zip(edits, targets, strict=True):
                provenance = (
                    merge_provenance([fact.provenance, exposure], fact.provenance.sensitivity)
                    if exposure
                    else fact.provenance
                )
                await tx.update(fact.id, edited_text(edit.text), provenance)

        await self._store.transact(notebook_id, run, author=author)

    async def delete(
        self, notebook_id: str, ids: Sequence[str], *, author: str | None = None
    ) -> None:
        async def run(tx: FactTx) -> None:
            await tx.remove(resolve_ids(await tx.current(), ids))

        await self._store.transact(notebook_id, run, author=author)

    async def clear(self, notebook_id: str, *, author: str | None = None) -> int:
        """Remove every current fact; history keeps them (clearing is not erasure)."""

        async def run(tx: FactTx) -> int:
            facts = await tx.current()
            await tx.remove(facts)
            return len(facts)

        return await self._store.transact(notebook_id, run, author=author)

    async def history(self, notebook_id: str) -> list[Revision]:
        return await self._store.revisions(notebook_id, limit=100_000)

    async def facts_at(self, notebook_id: str, revision: int) -> list[Fact]:
        state = state_at(await self.history(notebook_id), revision)
        return sorted(state.values(), key=lambda f: int(f.id))

    async def restore(self, notebook_id: str, revision: int, *, author: str | None = None) -> None:
        """Make the notebook's facts what they were at ``revision`` (as new revisions)."""

        async def run(tx: FactTx) -> None:
            target = state_at(await self._store.revisions(notebook_id, limit=100_000), revision)
            current = {f.id: f for f in await tx.current()}
            await tx.remove([f for i, f in current.items() if i not in target])
            for fact_id, fact in target.items():
                live = current.get(fact_id)
                if live is None:
                    await tx.revive(fact_id, fact.text, fact.provenance)
                elif live.text != fact.text or not live.provenance.same_as(fact.provenance):
                    # The restored text keeps any restriction the fact gained since.
                    floor = merge_provenance(
                        [fact.provenance, live.provenance], fact.provenance.sensitivity
                    )
                    await tx.update(fact_id, fact.text, floor)

        await self._store.transact(notebook_id, run, author=author)
