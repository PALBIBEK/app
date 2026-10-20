"""What can be done to a notebook's facts, each one locked transaction.

Capture deduplicates (a repeat only tightens provenance), edits and deletes name
facts by id (an unknown id fails the whole operation), clearing keeps history,
and restoring makes the facts what they were at a revision while keeping any
restriction a fact gained since. These run where the facts are stored, so a read
and the write that depends on it never cross the network apart.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import replace

from supervisor.memory_service.notebooks.errors import MemoryFactsMissing
from supervisor.memory_service.notebooks.render import (
    clean_captured,
    edited_text,
    normalize,
    parse_fact_id,
)
from supervisor.memory_service.notebooks.store import FactTx, NotebookStore
from supervisor.memory_service.notebooks.types import (
    CaptureMetadata,
    Edit,
    Fact,
    Provenance,
    Revision,
    capture_provenance,
    merge_provenance,
)


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


class NotebookOps:
    """The notebook operations over one store."""

    def __init__(self, store: NotebookStore) -> None:
        self.store = store

    async def capture(
        self,
        notebook_id: str,
        facts: Sequence[str],
        *,
        author: str | None = None,
        context: CaptureMetadata | None = None,
    ) -> int:
        """Add ``facts``; a repeat adds no text but may tighten what is known about it."""
        lines = clean_captured(facts)
        if not lines:
            return 0
        notebook = await self.store.get(notebook_id)
        provenance = capture_provenance(notebook.home, context)

        async def run(tx: FactTx) -> int:
            existing = {normalize(f.text): f for f in await tx.current()}
            added = 0
            for text in lines:
                key = normalize(text)
                if (duplicate := existing.get(key)) is not None:
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

        return await self.store.transact(notebook_id, run, author=author)

    async def edit(
        self,
        notebook_id: str,
        edits: Sequence[Edit],
        *,
        author: str | None = None,
        context: CaptureMetadata | None = None,
    ) -> None:
        notebook = await self.store.get(notebook_id)
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

        await self.store.transact(notebook_id, run, author=author)

    async def delete(self, notebook_id: str, ids: Sequence[str], *, author: str | None = None) -> None:
        async def run(tx: FactTx) -> None:
            await tx.remove(resolve_ids(await tx.current(), ids))

        await self.store.transact(notebook_id, run, author=author)

    async def clear(self, notebook_id: str, *, author: str | None = None) -> int:
        """Remove every current fact; history keeps them (clearing is not erasure)."""

        async def run(tx: FactTx) -> int:
            facts = await tx.current()
            await tx.remove(facts)
            return len(facts)

        return await self.store.transact(notebook_id, run, author=author)

    async def history(self, notebook_id: str) -> list[Revision]:
        return await self.store.revisions(notebook_id, limit=100_000)

    async def facts_at(self, notebook_id: str, revision: int) -> list[Fact]:
        state = state_at(await self.history(notebook_id), revision)
        return sorted(state.values(), key=lambda f: int(f.id))

    async def restore(self, notebook_id: str, revision: int, *, author: str | None = None) -> None:
        """Make the notebook's facts what they were at ``revision`` (as new revisions)."""

        async def run(tx: FactTx) -> None:
            target = state_at(await self.store.revisions(notebook_id, limit=100_000), revision)
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

        await self.store.transact(notebook_id, run, author=author)
