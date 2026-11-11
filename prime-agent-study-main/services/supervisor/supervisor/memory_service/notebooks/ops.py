"""What can be done to a notebook's facts, each one locked transaction.

Capture deduplicates (a repeat only tightens provenance), edits and deletes name
facts by id (an unknown id fails the whole operation), clearing keeps history,
and restoring makes the facts what they were at a revision while keeping any
restriction a fact gained since. These run where the facts are stored, so a read
and the write that depends on it never cross the network apart.
"""

from __future__ import annotations

import logging
import time
from collections import defaultdict
from collections.abc import Callable, Iterable, Sequence
from dataclasses import replace
from datetime import date
from typing import Final, Literal

import httpx
from sqlalchemy import select, update
from sqlalchemy.exc import DBAPIError, DisconnectionError, InterfaceError, OperationalError
from sqlalchemy.exc import TimeoutError as PoolTimeout

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
    NotebookNotFound,
    RevisionOutOfRange,
)
from supervisor.memory_service.notebooks.models import InboxRow
from supervisor.memory_service.notebooks.render import (
    clean_captured,
    edited_text,
    normalize,
    parse_fact_id,
)
from supervisor.memory_service.notebooks.store import (
    FactTx,
    NotebookStore,
    empty_set_aside,
    mark_handled,
    now_ms,
    row_provenance,
)
from supervisor.memory_service.notebooks.types import (
    CaptureMetadata,
    Edit,
    Fact,
    Notebook,
    Provenance,
    Revision,
    Typed,
    capture_provenance,
    merge_provenance,
)
from supervisor.memory_service.notebooks.wire import observation_in, observation_out, typed_of
from supervisor.memory_service.observations import (
    ObservationDraft,
    ObservationInvalid,
    fit_cleaned_statement,
)

logger = logging.getLogger(__name__)

PROMOTE_BATCH = 100
"""Queued captures one promotion lands, under one lock."""
_TRANSIENT: Final = (
    OperationalError, InterfaceError, PoolTimeout, DisconnectionError, OSError,  # TimeoutError among them
    httpx.TransportError,  # a remote embedder or bank out of reach: not the capture's fault
)
_TRANSIENT_STATES: Final = frozenset({"40001", "40P01", "55P03", "57014", "57P01", "57P02", "57P03"})
"""Postgres SQLSTATEs that pass: a serialization failure, a deadlock, a lock not to be had,
a statement timeout or cancel, the server shutting down (class 08, the connection, and
class 53, the server out of connections, memory or disk, too)."""


def is_transient(exc: BaseException) -> bool:
    """Whether a failure is the database's or the network's, which passes, never a
    capture's own: the connection, the pool, contention. asyncpg's deadlocks and timeouts
    arrive as a plain ``DBAPIError``, told by their SQLSTATE. A constraint or data error
    (``IntegrityError``, ``DataError``) is the capture's, and counted."""
    if isinstance(exc, _TRANSIENT):
        return True
    if isinstance(exc, DBAPIError):
        state = str(getattr(exc.orig, "sqlstate", None) or getattr(exc.orig, "pgcode", None) or "")
        return state in _TRANSIENT_STATES or state.startswith(("08", "53"))
    return False
PROMOTE_ATTEMPTS = 8
"""Tries a queued capture that fails to land on its own gets before it is set aside."""
FAILURE_SPACING_MS = 30_000
"""A queued capture's failures are counted at most this often: a busy notebook landing
others beside it (no backoff for it) and every replica's tries together still give it
``PROMOTE_ATTEMPTS`` over minutes, not one burst of passes."""
STUCK_AFTER_MS = 24 * 3600 * 1000
"""A queued capture failing this long is counted against it even while nothing lands."""
LANDING_EVIDENCE_S = 300.0
"""A lone queued capture failing is counted against it only when another landed on this
replica this recently: the service lands captures, so the fault is that capture's own."""
KEEP_SET_ASIDE_MS = 7 * 24 * 3600 * 1000
"""How long a set-aside capture is kept (to look into, and queue again) before it is pruned."""
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
        self._landed_at: float | None = None  # when a promotion here last landed a capture (monotonic)

    def _landed(self, captures: int) -> None:
        if captures:
            self._landed_at = time.monotonic()

    def _landing_lately(self) -> bool:
        return self._landed_at is not None and time.monotonic() - self._landed_at < LANDING_EVIDENCE_S

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
        notebook: Notebook | None = None,
    ) -> int:
        """Queue an automatic capture's typed observations for its notebook, taking no lock
        on it (``notebooks.inbox``); the facts land when the inbox is drained, and ``retire``
        (the facts they make false) goes after them, in the same transaction, only when
        every one of them was kept: a capture that does not land leaves both. Each statement
        is cleaned as ``capture`` cleans a fact (a model's "(said in X)" marked a claim) and
        every bound is checked here, so a capture is refused whole, never half kept. The
        number queued: 0 for a capture already queued under ``idempotency_key`` (a retried
        burst). ``notebook``: the notebook as the caller read it already (one read fewer)."""
        if not drafts and not retire:
            return 0
        # One that only retires (every statement a repeat, some fact made false) is queued
        # too: it lands in its place in the queue, as every capture's retirements do.
        given = bool(drafts)
        _within("facts in one capture", MAX_FACTS_PER_CAPTURE, len(drafts))
        _within("facts retired in one capture", MAX_FACTS_PER_CAPTURE, len(retire))
        retire_ids = [_fact_id(i) for i in retire]
        drafts = [c for c in (_cleaned(d) for d in drafts) if c is not None]
        if given and not drafts:
            raise InvalidObservation("every statement is empty")  # said, not a capture that quietly did nothing
        for draft in drafts:
            try:
                draft.validate()
            except ObservationInvalid as exc:
                raise InvalidObservation(str(exc)) from None
        _within_length([d.statement for d in drafts])
        expires_on = _expiry(context.expires_on if context else None) or None
        notebook = notebook or await self.store.get(notebook_id)
        if notebook.archived_at is not None:
            raise NotebookArchived(notebook_id)
        # One read: the notebook's size, and each fact to retire with its text now (it is
        # retired when the capture lands only if it still says that: a fact edited meanwhile
        # is not the one resolution judged false).
        live, queued, texts = await self.store.capacity(notebook_id, retire_ids)
        # A fact a draft restates is never retired (``_promote_row``): it frees no room.
        restated = {normalize(d.statement) for d in drafts}
        texts = {i: t for i, t in texts.items() if normalize(t) not in restated}
        if live + queued + len(drafts) - len(texts) > MAX_FACTS_PER_NOTEBOOK:
            # Past the limit, counting what is queued (so concurrent captures cannot all pass on
            # one count) and the room its retirements free: refused now, as ``capture``
            # refuses, unless what it adds that is new fits (a repeat adds nothing). Every
            # text is read only here, past the limit; a retried burst (its key queued already) is
            # never refused for its own queued facts.
            if idempotency_key is not None and await self.store.has_key(notebook_id, idempotency_key):
                return 0
            known = {normalize(t) for t in await self.store.all_texts(notebook_id)}
            pending = {normalize(t) for t in await self.store.queued_statements(notebook_id)} - known
            new = {normalize(d.statement) for d in drafts} - known - pending
            _within("facts in a notebook", MAX_FACTS_PER_NOTEBOOK, live + len(pending) + len(new) - len(texts))
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
            else:
                # No drainer here: an in-memory database (development, tests) runs no background
                # loop. Production always runs one, which retries what fails; here a failed
                # promotion waits for the next capture into this notebook.
                try:
                    await self.promote(notebook_id)
                except Exception:  # queued all the same: the next promotion lands it
                    logger.warning("promoting notebook %s's capture failed", notebook_id, exc_info=True)
        return len(drafts) if queued else 0

    async def promote(self, notebook_id: str, *, batch: int = PROMOTE_BATCH) -> tuple[int, int]:
        """Land up to ``batch`` of the notebook's queued captures as facts, in one
        transaction under its lock, each as ``capture`` would (repeats tighten, never
        duplicate); then mark them promoted. (captures, facts added). Replicas may promote
        the same notebook at once: its lock makes them take turns, and the second finds the
        first's captures gone.

        Their facts are embedded first, outside the lock (the embedder caches them), so the
        lock is held for the writes only. A batch that fails is rolled back whole and its
        captures landed one at a time instead: the good ones land, and one that fails on its
        own again is counted against it and set aside at ``PROMOTE_ATTEMPTS`` (the drainer
        spaces the tries); the failure is raised, for the drainer to back off."""
        if not await self.store.queued_ids(notebook_id, limit=1):
            return 0, 0  # landed already (another replica's turn): never the notebook's lock for nothing
        try:
            await self.store.embed_queued(notebook_id, limit=batch)
        except Exception:  # an aid only: landing embeds again, where a capture's own failure is handled
            logger.warning("notebook %s: embedding queued facts ahead failed", notebook_id, exc_info=True)
        try:
            return await self._promote_batch(notebook_id, batch)
        except (NotebookArchived, NotebookNotFound) as exc:
            return await self._notebook_gone(notebook_id, exc)
        except Exception as exc:
            if is_transient(exc):
                raise  # the database's contention or connection, not a capture: nothing counted
            logger.warning("notebook %s: a batch of queued captures failed: landing them one at a time",
                           notebook_id, exc_info=True)
        # Each on its own, in its own transaction: the good ones land, one that fails is counted
        # against itself alone, and none holds up the captures queued behind it.
        captures = added = 0
        failed: list[tuple[int, BaseException]] = []
        interrupted: BaseException | None = None  # a transient fault: the rest wait for next time
        queued = await self.store.queued_ids(notebook_id, limit=batch)
        tried = 0
        for row_id in queued:
            tried += 1
            try:
                one, facts = await self._promote_batch(notebook_id, 1, only=row_id)
            except (NotebookArchived, NotebookNotFound) as exc:  # the notebook's, not the capture's
                dropped, _ = await self._notebook_gone(notebook_id, exc)
                return captures + dropped, added  # with those landed before it went
            except Exception as exc:  # noqa: BLE001 — each is counted and logged below, or raised
                if is_transient(exc):
                    interrupted, tried = exc, tried - 1  # not this capture's: not tried
                    break
                failed.append((row_id, exc))
                continue
            captures, added = captures + one, added + facts
        if not failed:
            if interrupted is not None and not captures:
                raise interrupted
            return captures, added  # what landed before a transient fault stands
        # The failures gathered before a transient fault are counted as any others.
        if len(failed) == tried and not self._landing_lately():
            # Every one of them fails, each on its own, while nothing lands here: the fault is
            # not theirs (a bug, a bank or an embedder down for all). Nothing is counted, so
            # none is lost to it. While captures land elsewhere, each failure is its own; and
            # one failing for a day (an outage that long is seen) is counted all the same,
            # never retried for good: set aside, it is kept and can be queued again.
            exc_of_all = failed[0][1]
            stale = await self.store.received_before([r for r, _ in failed], before=now_ms() - STUCK_AFTER_MS)
            failed = [(r, e) for r, e in failed if r in stale]
            if not failed:
                logger.warning("notebook %s: every queued capture failed to land: none counted", notebook_id)
                raise exc_of_all

        for row_id, error in failed:
            attempts = await self.store.count_failure(
                row_id, give_up_at=PROMOTE_ATTEMPTS, spacing_ms=FAILURE_SPACING_MS
            )
            logger.warning(
                "notebook %s: queued capture %s failed to land on its own (%d of %d)%s",
                notebook_id, row_id, attempts, PROMOTE_ATTEMPTS,
                ": set aside" if attempts >= PROMOTE_ATTEMPTS else "", exc_info=error,
            )
        if captures:
            return captures, added  # others landed: the notebook is not backed off for the failed one
        raise interrupted or failed[0][1]  # for the drainer to back off before the next try

    async def _notebook_gone(self, notebook_id: str, exc: NotebookArchived | NotebookNotFound) -> tuple[int, int]:
        """The notebook's queue when the notebook itself is past taking it, which is no
        capture's fault: an archived one's is dropped, and an erased one's (a database that
        does not enforce the foreign key) deleted, nothing of the erased owner kept."""
        if isinstance(exc, NotebookArchived):
            dropped = await self.store.drop_inbox(notebook_id)
            logger.info("notebook %s was archived with %d captures queued: not kept", notebook_id, dropped)
            return dropped, 0
        deleted = await self.store.delete_inbox(notebook_id)
        logger.info("notebook %s is gone with %d captures queued: deleted", notebook_id, deleted)
        return deleted, 0

    async def _promote_batch(self, notebook_id: str, batch: int, *, only: int | None = None) -> tuple[int, int]:
        """One transaction: up to ``batch`` of the queued captures (or the one ``only``
        names), landed and marked."""

        async def run(tx: FactTx) -> tuple[int, int, int]:
            query = select(InboxRow).where(InboxRow.notebook_id == notebook_id, InboxRow.promoted_at.is_(None))
            if only is not None:
                query = query.where(InboxRow.id == only)
            rows = list(await tx.db.scalars(query.order_by(InboxRow.id).limit(batch)))
            added = 0
            current = await tx.current() if rows else []
            existing = {normalize(f.text): f for f in current}  # once a batch, kept current
            by_id = {f.id: f for f in current}  # every live fact, two of one text included
            unreadable = []
            for row in rows:
                drafts = _readable(notebook_id, row)
                if drafts is None:  # one that cannot be read never will be: set aside, not the queue
                    unreadable.append(row.id)
                else:
                    added += await _promote_row(tx, row, drafts, existing, by_id)
            # Any other failure (the database, the embedder) fails the whole batch, rolled back
            # with nothing marked: the drainer backs the notebook off and tries it again.
            if unreadable:  # set aside as one that keeps failing is: kept, counted, to look into
                await tx.db.execute(
                    update(InboxRow).where(InboxRow.id.in_(unreadable)).values(promoted_at=tx.at, attempts=PROMOTE_ATTEMPTS)
                )
            landed = [r.id for r in rows if r.id not in unreadable]
            if landed:
                await tx.db.execute(mark_handled(InboxRow.id.in_(landed), at=tx.at))
            return len(rows), added, len(landed)

        captures, added, landed = await self.store.transact(notebook_id, run)
        self._landed(landed)  # evidence the service lands captures: never one only set aside
        return captures, added

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
            current = await tx.current()
            targets = resolve_ids(current, [e.id for e in edits])
            rewritten = {f.id: edited_text(e.text) for e, f in zip(edits, targets, strict=True)}
            await _forget_queued(tx, [f.text for f in current], [rewritten.get(f.id, f.text) for f in current])
            for edit, fact in zip(edits, targets, strict=True):
                provenance = (
                    merge_provenance([fact.provenance, exposure], fact.provenance.sensitivity)
                    if exposure
                    else fact.provenance
                )
                await tx.update(fact.id, edited_text(edit.text), provenance)
                if fact.typed is not None and edited_text(edit.text) != fact.text:
                    # A person's words now, not the observation's: what it said of itself
                    # (its keywords, its body, the model that made it) no longer describes it.
                    await tx.set_typed(fact, None)
                if expiry is not None and fact.expires_on != (expiry or None):
                    await tx.set_expiry(replace(fact, text=edited_text(edit.text), provenance=provenance), expiry or None)

        await self.store.transact(notebook_id, run, author=author)

    async def delete(self, notebook_id: str, ids: Sequence[str], *, author: str | None = None) -> None:
        async def run(tx: FactTx) -> None:
            current = await tx.current()
            removed = resolve_ids(current, ids)
            await tx.remove(removed)
            gone = {f.id for f in removed}
            await _forget_queued(tx, [f.text for f in current], [f.text for f in current if f.id not in gone])

        await self.store.transact(notebook_id, run, author=author)

    async def clear(self, notebook_id: str, *, author: str | None = None) -> int:
        """Remove every current fact; history keeps them (clearing is not erasure). Captures
        queued before it go with them, in its transaction: none lands afterwards to bring
        back what was cleared."""

        async def run(tx: FactTx) -> int:
            facts = await tx.current()
            await tx.remove(facts)
            # Marked as handled, not deleted: a retried burst still finds its key (and lands
            # nothing); pruned with the rest in time.
            await tx.db.execute(mark_handled(InboxRow.notebook_id == notebook_id, at=tx.at))
            # Set-aside ones too, emptied: none queued again later brings back what was cleared.
            await tx.db.execute(empty_set_aside(InboxRow.notebook_id == notebook_id))
            return len(facts)

        return await self.store.transact(notebook_id, run, author=author)

    async def history(self, notebook_id: str) -> list[Revision]:
        return await self.store.history(notebook_id)

    async def facts_at(self, notebook_id: str, revision: int) -> list[Fact]:
        """The facts as they were at ``revision`` (0, before any change, to the current one)."""
        _in_range(revision, (await self.store.get(notebook_id)).revision)
        state = state_at(await self.history(notebook_id), revision)
        # A fact's type is kept on its row (deleted facts too), describing its words now: the
        # facts as they were carry it only where they said those words.
        typed = await self.store.typed_of(notebook_id, list(state))
        def at_the_time(fact: Fact) -> Fact:
            words, kind = typed.get(fact.id, (None, None))
            return replace(fact, typed=kind if words == fact.text else None)

        facts = [at_the_time(f) for f in state.values()]
        return sorted(facts, key=lambda f: int(f.id))

    async def restore(self, notebook_id: str, revision: int, *, author: str | None = None) -> None:
        """Make the notebook's facts what they were at ``revision`` (as new revisions)."""

        async def run(tx: FactTx) -> None:
            _in_range(revision, int(tx.notebook.revision))
            target = state_at(await tx.history(), revision)
            current = {f.id: f for f in await tx.current()}
            removed = [f for i, f in current.items() if i not in target]
            await tx.remove(removed)
            await _forget_queued(tx, [f.text for f in current.values()], [f.text for f in target.values()])
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
                    if live.typed is not None and live.text != fact.text:
                        await tx.set_typed(live, None)  # its type described the words it no longer has

        await self.store.transact(notebook_id, run, author=author)


async def _forget_queued(tx: FactTx, before: Iterable[str], after: Iterable[str]) -> None:
    """Take out of the notebook's queued (and set-aside) captures what restates words a
    person just deleted or rewrote: in ``before`` (the facts' words before the change), in
    no fact ``after`` it. Queued before the change, such a capture would land after it and
    bring them back; before the inbox it had landed first, and the change undid it. Words a
    fact still has (a case edit, another fact with the same text) are kept, to tighten it.

    Under the notebook's lock, in the change's transaction. A capture that loses any
    statement loses its retirements too: they were judged with what replaced them, and
    which statement replaced which fact is not known, so (as when a capture does not land)
    both facts are kept. One left with no statement is handled."""
    gone = {normalize(t) for t in before} - {normalize(t) for t in after}
    if not gone:
        return
    columns = select(InboxRow.id, InboxRow.observations, InboxRow.retire, InboxRow.promoted_at)
    # Two reads, each on its partial index: the queue, and the few set aside.
    queued = await tx.db.execute(columns.where(InboxRow.notebook_id == tx.notebook.id, InboxRow.promoted_at.is_(None)))
    set_aside = await tx.db.execute(
        columns.where(InboxRow.notebook_id == tx.notebook.id, InboxRow.attempts >= PROMOTE_ATTEMPTS)
    )
    for row_id, observations, _retire, promoted_at in [*queued.all(), *set_aside.all()]:
        if not isinstance(observations, list):
            continue  # stored by another version: unreadable, set aside as such (``_readable``)
        kept = [o for o in observations if not (isinstance(o, dict) and normalize(str(o.get("statement", ""))) in gone)]
        if len(kept) == len(observations):
            continue
        if kept:
            await tx.db.execute(
                update(InboxRow).where(InboxRow.id == row_id).values(observations=kept, size=len(kept), retire=[])
            )
        elif promoted_at is None:
            await tx.db.execute(mark_handled(InboxRow.id == row_id, at=tx.at))
        else:  # set aside, now with nothing to land: never queued again
            await tx.db.execute(empty_set_aside(InboxRow.id == row_id))


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
    room: int = 0,
    by_id: dict[str, Fact] | None = None,
) -> tuple[int, bool]:
    """Add each (text, type) to the notebook, under the caller's lock: (facts added,
    whether every entry was kept).

    A repeat adds no text but may tighten what is known about the fact (its provenance,
    its expiry, and the type a plain-text fact lacked). Past the notebook's size limit a
    capture is refused (``"refuse"``); a queued one, already accepted, keeps what fits
    (``"drop"``), the rest logged. ``existing`` (normalized text to live fact) is read here
    when not given, and kept current: a batch reads the notebook once."""
    if existing is None:
        current = await tx.current()
        existing = {normalize(f.text): f for f in current}
        by_id = {f.id: f for f in current} if by_id is None else by_id
    # The limit counts live facts, two of one text both (``existing`` holds one per text).
    live = len(by_id) if by_id is not None else len(existing)
    added = dropped = 0
    for text, typed in entries:
        key = normalize(text)
        if (duplicate := existing.get(key)) is not None:
            merged = merge_provenance([duplicate.provenance, provenance], duplicate.provenance.sensitivity)
            if not merged.same_as(duplicate.provenance):
                await tx.update(duplicate.id, duplicate.text, merged)
                duplicate = existing[key] = replace(duplicate, provenance=merged)
            # A repeat brings its own expiry; one with none restates an expired fact as true again.
            if (expires_on is not None and duplicate.expires_on != expires_on) or (
                expires_on is None and duplicate.expired()
            ):
                await tx.set_expiry(duplicate, expires_on)
                duplicate = existing[key] = replace(duplicate, expires_on=expires_on)
            if typed is not None and duplicate.typed is None:
                await tx.set_typed(duplicate, typed)
                duplicate = existing[key] = replace(duplicate, typed=typed)
            if by_id is not None:
                by_id[duplicate.id] = duplicate
            continue
        if live + 1 > MAX_FACTS_PER_NOTEBOOK + room:
            if over_limit == "drop":
                dropped += 1  # not kept; the entries after it are still read (a repeat merges)
                continue
            _within("facts in a notebook", MAX_FACTS_PER_NOTEBOOK, live + 1)
        fact_id = await tx.insert(text, provenance, expires_on=expires_on, typed=typed)
        existing[key] = Fact(fact_id, text, provenance, tx.at, tx.at, tx.author, expires_on, typed)
        if by_id is not None:  # the caller's id index, kept current
            by_id[fact_id] = existing[key]
        live += 1
        added += 1
    if dropped:
        logger.warning(
            "notebook %s is full (%d facts): %d queued facts not kept", tx.notebook.id, MAX_FACTS_PER_NOTEBOOK, dropped
        )
    return added, not dropped


def _cleaned(draft: ObservationDraft) -> ObservationDraft | None:
    """The draft with its statement cleaned as ``capture`` cleans a fact
    (``render.clean_captured``); None for one cleaned to nothing. A client cleans first
    (``observations.parse_model_outcome``); one that did not may have a statement the
    cleaning lengthened past its bound: cut to it, never the whole capture refused."""
    cleaned = clean_captured([draft.statement])
    if not cleaned:
        return None
    return replace(draft, statement=fit_cleaned_statement(draft.statement, cleaned[0]))


def _readable(notebook_id: str, row: InboxRow) -> list[ObservationDraft] | None:
    """A queued capture's observations, each within its bounds; None (logged) for one that
    cannot be read (stored by another version, say): that does not change, so it is set
    aside at once rather than retried."""
    try:
        drafts = [observation_in(o) for o in row.observations]
        for draft in drafts:
            draft.validate()
        for entry in row.retire or ():
            if not (isinstance(entry.get("id"), str) and isinstance(entry.get("text"), str)):
                raise ObservationInvalid(f"retire entry {entry!r}")
    except (ObservationInvalid, TypeError, AttributeError):
        logger.warning("notebook %s: queued capture %s cannot be read: set aside", notebook_id, row.id, exc_info=True)
        return None
    return drafts


async def _promote_row(
    tx: FactTx, row: InboxRow, drafts: Sequence[ObservationDraft], existing: dict[str, Fact], by_id: dict[str, Fact]
) -> int:
    """Land one queued capture, then retire what it makes false (only when all of it was
    kept, only facts that still say what resolution judged, and never one the capture
    itself just restated); the facts added."""
    tx.author = row.author
    provenance = row_provenance(row)
    entries = [(d.statement, typed_of(d)) for d in drafts]
    restated = {normalize(text) for text, _ in entries}
    gone = [
        by_id[r["id"]]
        for r in row.retire or ()
        if r["id"] in by_id and by_id[r["id"]].text == r["text"] and normalize(by_id[r["id"]].text) not in restated
    ]
    # What it retires makes room for what replaces it (as observe counted): a full notebook
    # takes the replacement, then loses the fact it replaces. Only when all of it fits:
    # otherwise it lands what fits within the limit and retires nothing (never left over it).
    new = len({key for key in restated if key not in existing})
    room = len(gone) if len(by_id) + new <= MAX_FACTS_PER_NOTEBOOK + len(gone) else 0  # live facts, as land counts
    landed, whole = await land(
        tx, entries, provenance, row.expires_on, existing=existing, over_limit="drop", room=room, by_id=by_id
    )
    if gone and whole:
        await tx.remove(gone)
        for fact in gone:
            del by_id[fact.id]
            key = normalize(fact.text)
            if key in existing and existing[key].id == fact.id:
                # Its twin (another live fact of the same text) stands for the text now, so a
                # later capture in the batch merges into it rather than adding a third.
                if (twin := next((f for f in by_id.values() if normalize(f.text) == key), None)) is not None:
                    existing[key] = twin
                else:
                    del existing[key]
    return landed


def _fact_id(value: str) -> str:
    """A fact id as sent (``#12`` or ``12``); anything else is not one."""
    fact_id = parse_fact_id(value)
    if not fact_id.isdigit() or int(fact_id) > _MAX_ID:
        raise InvalidObservation(f"not a fact id: {value!r}")
    return fact_id


_MAX_ID = 2**63 - 1
