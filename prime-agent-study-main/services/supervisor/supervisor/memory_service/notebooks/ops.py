"""What can be done to a notebook's facts, each one locked transaction.

Capture deduplicates (a repeat only tightens provenance), edits and deletes name
facts by id (an unknown id fails the whole operation), clearing keeps history,
and restoring makes the facts what they were at a revision while keeping any
restriction a fact gained since. These run where the facts are stored, so a read
and the write that depends on it never cross the network apart.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Callable, Sequence
from dataclasses import replace
from datetime import date
from typing import Literal

from sqlalchemy import select, update

from supervisor.memory_service.limits import (
    MAX_FACT_CHARS,
    MAX_FACTS_PER_CAPTURE,
    MAX_FACTS_PER_NOTEBOOK,
)
from supervisor.memory_service.notebooks.errors import (
    EmptyFact,
    InvalidExpiry,
    InvalidObservation,
    MemoryFactsMissing,
    MemoryLimitExceeded,
    NotebookArchived,
    RevisionOutOfRange,
)
from supervisor.memory_service.notebooks.models import InboxRow
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
    Sensitivity,
    Typed,
    capture_provenance,
    merge_provenance,
)
from supervisor.memory_service.notebooks.wire import observation_in, observation_out, typed_of
from supervisor.memory_service.observations import (
    MAX_STATEMENT_BYTES,
    ObservationDraft,
    ObservationInvalid,
    truncate_utf8,
)

logger = logging.getLogger(__name__)

PROMOTE_BATCH = 100
"""Queued captures one promotion lands, under one lock."""
KEEP_PROMOTED_MS = 24 * 3600 * 1000
"""How long a promoted capture's row is kept, so a retried burst (its idempotency key)
is still recognised: well past capture's own retries and Temporal's."""


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
        self.on_queued: Callable[[], None] | None = None
        """Told when a capture is queued: the inbox's drainer, once it runs
        (``notebooks.inbox``). Without one, ``observe`` lands its capture itself."""

    async def capture(
        self,
        notebook_id: str,
        facts: Sequence[str],
        *,
        author: str | None = None,
        context: CaptureMetadata | None = None,
    ) -> int:
        """Add ``facts``; a repeat adds no text but may tighten what is known about it.

        ``context.expires_on`` makes the captured facts time-bound (a repeat too)."""
        lines = clean_captured(facts)
        if not lines:
            return 0
        _within("facts in one capture", MAX_FACTS_PER_CAPTURE, len(lines))
        _within_length(lines)
        expires_on = _expiry(context.expires_on if context else None) or None
        notebook = await self.store.get(notebook_id)
        provenance = capture_provenance(notebook.home, context)

        async def run(tx: FactTx) -> int:
            added, _ = await land(tx, [(text, None) for text in lines], provenance, expires_on)
            return added

        return await self.store.transact(notebook_id, run, author=author)

    async def observe(
        self,
        notebook_id: str,
        drafts: Sequence[ObservationDraft],
        *,
        author: str | None = None,
        context: CaptureMetadata | None = None,
        retire: Sequence[str] = (),
        idempotency_key: str | None = None,
    ) -> int:
        """Queue an automatic capture's typed observations for its notebook, taking no lock
        on it (``notebooks.inbox``); the facts land when the inbox is drained, and ``retire``
        (the facts they make false) goes after them, in the same transaction, only when
        every one of them was kept: a capture that does not land leaves both. Each statement
        is cleaned as ``capture`` cleans a fact (a model's "(said in X)" marked a claim) and
        every bound is checked here, so a capture is refused whole, never half kept. The
        number queued: 0 for a capture already queued under ``idempotency_key`` (a retried
        burst)."""
        if not drafts:
            return 0
        _within("facts in one capture", MAX_FACTS_PER_CAPTURE, len(drafts))
        drafts = [c for c in (_cleaned(d) for d in drafts) if c is not None]
        if not drafts:
            return 0
        for draft in drafts:
            try:
                draft.validate()
            except ObservationInvalid as exc:
                raise InvalidObservation(str(exc)) from None
        _within_length([d.statement for d in drafts])
        expires_on = _expiry(context.expires_on if context else None) or None
        notebook = await self.store.get(notebook_id)
        if notebook.archived_at is not None:
            raise NotebookArchived(notebook_id)
        # A full notebook refuses the capture now, as ``capture`` does, rather than accept
        # it and keep none of it when it lands (repeats aside; one it fills meanwhile keeps
        # what fits, then).
        _within("facts in a notebook", MAX_FACTS_PER_NOTEBOOK, await self.store.live_count(notebook_id) + 1)
        # Each fact to retire with its text now: it is retired when the capture lands only if
        # it still says that (a fact edited meanwhile is not the one resolution judged false).
        texts = await self.store.texts(notebook_id, [parse_fact_id(i) for i in retire])
        retired = [{"id": fact_id, "text": text} for fact_id, text in texts.items()]
        provenance = capture_provenance(notebook.home, context)
        queued = await self.store.append_inbox(
            notebook_id,
            [observation_out(d) for d in drafts],
            provenance,
            author=author,
            expires_on=expires_on,
            retire=retired,
            idempotency_key=idempotency_key,
        )
        if queued:
            if self.on_queued is not None:
                self.on_queued()
            else:  # no drainer here (an in-memory database runs no background loop): land it now
                try:
                    await self.promote(notebook_id)
                except Exception:  # queued all the same: the next promotion lands it
                    logger.warning("promoting notebook %s's capture failed", notebook_id, exc_info=True)
        return len(drafts) if queued else 0

    async def promote(self, notebook_id: str, *, batch: int = PROMOTE_BATCH) -> tuple[int, int]:
        """Land up to ``batch`` of the notebook's queued captures as facts, in one
        transaction under its lock, each as ``capture`` would (repeats tighten, never
        duplicate); then mark them promoted. (captures, facts added). Replicas may promote the same notebook at once: its lock makes them take
        turns, and the second finds the first's captures gone."""

        async def run(tx: FactTx) -> tuple[int, int]:
            rows = list(
                await tx.db.scalars(
                    select(InboxRow)
                    .where(InboxRow.notebook_id == notebook_id, InboxRow.promoted_at.is_(None))
                    .order_by(InboxRow.id)
                    .limit(batch)
                )
            )
            added = 0
            existing = await live_facts(tx) if rows else {}  # once a batch, kept current
            for row in rows:
                tx.author = row.author
                drafts = [observation_in(o) for o in row.observations]
                provenance = Provenance(
                    Sensitivity.parse(row.sensitivity), tuple(row.sources or ()), bool(row.source_unknown)
                )
                landed, whole = await land(
                    tx,
                    [(d.statement, typed_of(d)) for d in drafts],
                    provenance,
                    row.expires_on,
                    existing=existing,
                    over_limit="drop",
                )
                added += landed
                if row.retire and whole:  # after what replaces them, and only when it was kept
                    live = {f.id: (k, f) for k, f in existing.items()}
                    gone = [
                        live[r["id"]]
                        for r in row.retire
                        # gone already, or edited since resolution judged it: left as it is
                        if r["id"] in live and live[r["id"]][1].text == r["text"]
                    ]
                    await tx.remove([f for _, f in gone])
                    for key, _ in gone:
                        del existing[key]
            if rows:
                await tx.db.execute(
                    update(InboxRow).where(InboxRow.id.in_([r.id for r in rows])).values(promoted_at=tx.at)
                )
            return len(rows), added

        try:
            return await self.store.transact(notebook_id, run)
        except NotebookArchived:
            dropped = await self.store.drop_inbox(notebook_id)
            logger.info("notebook %s was archived with %d captures queued: not kept", notebook_id, dropped)
            return dropped, 0

    async def edit(
        self,
        notebook_id: str,
        edits: Sequence[Edit],
        *,
        author: str | None = None,
        context: CaptureMetadata | None = None,
    ) -> None:
        _within("edits in one call", MAX_FACTS_PER_CAPTURE, len(edits))
        _within_length([e.text for e in edits])
        if blank := [e.id for e in edits if not edited_text(e.text)]:
            raise EmptyFact(blank)
        expiry = _expiry(context.expires_on if context else None)  # None: keep; "": clear
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
                if expiry is not None and fact.expires_on != (expiry or None):
                    await tx.set_expiry(replace(fact, text=edited_text(edit.text), provenance=provenance), expiry or None)

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
        return await self.store.history(notebook_id)

    async def facts_at(self, notebook_id: str, revision: int) -> list[Fact]:
        """The facts as they were at ``revision`` (0, before any change, to the current one)."""
        _in_range(revision, (await self.store.get(notebook_id)).revision)
        state = state_at(await self.history(notebook_id), revision)
        # A fact's type says what it is, not what it said: it is the fact's own, kept on its
        # row (deleted facts too), so the facts as they were carry it.
        typed = await self.store.typed_of(notebook_id, list(state))
        facts = [replace(f, typed=typed.get(f.id)) for f in state.values()]
        return sorted(facts, key=lambda f: int(f.id))

    async def restore(self, notebook_id: str, revision: int, *, author: str | None = None) -> None:
        """Make the notebook's facts what they were at ``revision`` (as new revisions)."""

        async def run(tx: FactTx) -> None:
            _in_range(revision, int(tx.notebook.revision))
            target = state_at(await tx.history(), revision)
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


def _within(what: str, limit: int, got: int) -> None:
    if got > limit:
        raise MemoryLimitExceeded(what, limit, got)


def _within_length(texts: Sequence[str]) -> None:
    longest = max((len(t) for t in texts), default=0)
    _within("characters in a fact", MAX_FACT_CHARS, longest)


def _expiry(value: str | None) -> str | None:
    """A day as YYYY-MM-DD (mem0's _normalize_expiration_date); "" (clear) and None pass."""
    if value is None or value == "":
        return value
    try:
        return date.fromisoformat(value.strip()).isoformat()
    except ValueError as exc:
        raise InvalidExpiry(value) from exc


def _in_range(revision: int, current: int) -> None:
    if not 0 <= revision <= current:
        raise RevisionOutOfRange(revision, current)


async def land(
    tx: FactTx,
    entries: Sequence[tuple[str, Typed | None]],
    provenance: Provenance,
    expires_on: str | None,
    *,
    existing: dict[str, Fact] | None = None,
    over_limit: Literal["refuse", "drop"] = "refuse",
) -> tuple[int, bool]:
    """Add each (text, type) to the notebook, under the caller's lock: (facts added,
    whether every entry was kept).

    A repeat adds no text but may tighten what is known about the fact (its provenance,
    its expiry, and the type a plain-text fact lacked). Past the notebook's size limit a
    capture is refused (``"refuse"``); a queued one, already accepted, keeps what fits
    (``"drop"``), the rest logged. ``existing`` (normalized text to live fact) is read here
    when not given, and kept current: a batch reads the notebook once."""
    if existing is None:
        existing = await live_facts(tx)
    added = 0
    for n, (text, typed) in enumerate(entries):
        key = normalize(text)
        if (duplicate := existing.get(key)) is not None:
            merged = merge_provenance([duplicate.provenance, provenance], duplicate.provenance.sensitivity)
            if not merged.same_as(duplicate.provenance):
                await tx.update(duplicate.id, duplicate.text, merged)
                duplicate = existing[key] = replace(duplicate, provenance=merged)
            if expires_on is not None and duplicate.expires_on != expires_on:
                await tx.set_expiry(duplicate, expires_on)
            if typed is not None and duplicate.typed is None:
                await tx.set_typed(duplicate, typed)
                duplicate = existing[key] = replace(duplicate, typed=typed)
            continue
        if len(existing) + 1 > MAX_FACTS_PER_NOTEBOOK:
            if over_limit == "drop":
                logger.warning(
                    "notebook %s is full (%d facts): %d queued facts not kept",
                    tx.notebook.id, MAX_FACTS_PER_NOTEBOOK, len(entries) - n,
                )
                return added, False
            _within("facts in a notebook", MAX_FACTS_PER_NOTEBOOK, len(existing) + 1)
        fact_id = await tx.insert(text, provenance, expires_on=expires_on, typed=typed)
        existing[key] = Fact(fact_id, text, provenance, tx.at, tx.at, tx.author, expires_on, typed)
        added += 1
    return added, True


async def live_facts(tx: FactTx) -> dict[str, Fact]:
    """The notebook's live facts by the key a repeat is found by."""
    return {normalize(f.text): f for f in await tx.current()}


def _cleaned(draft: ObservationDraft) -> ObservationDraft | None:
    """The draft with its statement cleaned as ``capture`` cleans a fact
    (``render.clean_captured``); None for one cleaned to nothing. A client cleans first
    (``observations.parse_model_outcome``); one that did not may have a statement the
    cleaning lengthened past its bound: cut to it, never the whole capture refused."""
    cleaned = clean_captured([draft.statement])
    if not cleaned:
        return None
    statement = cleaned[0]
    if _bytes(draft.statement) <= MAX_STATEMENT_BYTES < _bytes(statement):  # lengthened by the cleaning only
        statement = truncate_utf8(statement, MAX_STATEMENT_BYTES).rstrip()
    return replace(draft, statement=statement)


def _bytes(text: str) -> int:
    return len(text.encode("utf-8"))
