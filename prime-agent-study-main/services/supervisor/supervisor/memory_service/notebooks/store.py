"""SQL storage for notebooks and facts, and their place in the semantic bank.

Every change runs in one transaction that first writes the notebook row (a no-op
``revision = revision``): a row lock on Postgres, the database write lock on
SQLite, so concurrent writers serialise before they read. It then bumps the
notebook's revision once if anything changed, and appends one revision row per
fact change. The revision log is what history, restore and stale-write checks
stand on.

In the same transaction each live fact keeps one item in the semantic bank
(``memory_item``), so recall by meaning finds notebook facts and erasure removes
them. Only facts that are ``ordinary``, fully traced and learned from the owner
alone are indexed: the bank's recall applies no audience rules, so anything
narrower stays reachable only through notebook recall, which does.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field

from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from supervisor.memory_service.cost import CachingEmbedder
from supervisor.memory_service.embed import Embedder, content_hash
from supervisor.memory_service.models import MemoryItemRow
from supervisor.memory_service.notebooks.errors import (
    NotebookArchived,
    NotebookExists,
    NotebookNotFound,
    RevisionConflict,
)
from supervisor.memory_service.notebooks.models import (
    FactItemRow,
    FactRevisionRow,
    FactRow,
    InboxRow,
    NotebookRow,
)
from supervisor.memory_service.notebooks.types import (
    Fact,
    Notebook,
    NotebookStats,
    Provenance,
    Revision,
    Sensitivity,
    Typed,
    notebook_id,
    personal_source,
    subject_of,
)

BANK_CONFIDENCE = 0.9
"""A fact a user or agent wrote down is surer than one extracted from a transcript."""


def now_ms() -> int:
    return int(time.time() * 1000)


def _provenance(row: FactRow | FactRevisionRow) -> Provenance:
    return Provenance(
        Sensitivity.parse(row.sensitivity), tuple(row.sources or ()), bool(row.source_unknown)
    )


def _fact(row: FactRow) -> Fact:
    return Fact(
        id=str(row.id),
        text=row.text,
        provenance=_provenance(row),
        created_at=int(row.created_at),
        updated_at=int(row.updated_at),
        author=row.author,
        expires_on=row.expires_on,
        typed=_typed(row),
    )


def _typed(row: FactRow) -> Typed | None:
    if row.kind is None:
        return None
    return Typed(
        kind=row.kind,
        topic=row.topic,
        keywords=tuple(row.keywords or ()),
        aliases=tuple(row.aliases or ()),
        body=row.body,
        extraction_model=row.extraction_model,
        prompt_version=row.prompt_version,
    )


def _typed_columns(typed: Typed | None) -> dict[str, object]:
    if typed is None:
        return {}
    return {
        "kind": typed.kind,
        "topic": typed.topic,
        "keywords": list(typed.keywords),
        "aliases": list(typed.aliases),
        "body": typed.body,
        "extraction_model": typed.extraction_model,
        "prompt_version": typed.prompt_version,
    }


def _notebook(row: NotebookRow) -> Notebook:
    return Notebook(
        id=row.id,
        tenant_id=row.tenant_id,
        owner=row.owner_principal_id,
        agent_pin=row.agent_pin,
        name=row.name,
        label=row.label,
        revision=int(row.revision),
        created_at=int(row.created_at),
        archived_at=row.archived_at,
        last_tidied_at=row.last_tidied_at,
        agent=row.agent,
    )


def _revision(row: FactRevisionRow) -> Revision:
    return Revision(
        revision=int(row.revision),
        fact_id=str(row.fact_id),
        op=row.op,
        text=row.text,
        provenance=_provenance(row),
        at=int(row.at),
        author=row.author,
    )


def _columns(provenance: Provenance) -> dict[str, object]:
    return {
        "sensitivity": provenance.sensitivity.value,
        "sources": list(provenance.sources),
        "source_unknown": provenance.source_unknown,
    }


def bankable(notebook: NotebookRow, provenance: Provenance, expires_on: str | None = None) -> bool:
    """Whether a fact may sit in the bank, whose recall applies no audience rules (an
    archived notebook's facts do not: archiving takes them out of recall; nor does a
    time-bound fact: bank items have no expiry, notebook recall honours it)."""
    home = personal_source(notebook.owner_principal_id)
    return (
        notebook.archived_at is None
        and expires_on is None
        and provenance.sensitivity is Sensitivity.ORDINARY
        and not provenance.source_unknown
        and set(provenance.sources) <= {home}
    )


async def _revisions(db: AsyncSession, notebook_id: str, *, after: int, limit: int) -> list[Revision]:
    rows = list(
        await db.scalars(
            select(FactRevisionRow)
            .where(FactRevisionRow.notebook_id == notebook_id, FactRevisionRow.revision > after)
            .order_by(FactRevisionRow.revision, FactRevisionRow.id)
            .limit(limit)
        )
    )
    if len(rows) == limit:  # the page is full: finish its last revision
        last = rows[-1]
        rows += await db.scalars(
            select(FactRevisionRow)
            .where(
                FactRevisionRow.notebook_id == notebook_id,
                FactRevisionRow.revision == last.revision,
                FactRevisionRow.id > last.id,
            )
            .order_by(FactRevisionRow.id)
        )
    return [_revision(r) for r in rows]


async def _history(db: AsyncSession, notebook_id: str, *, page: int) -> list[Revision]:
    out: list[Revision] = []
    after = 0
    while True:
        rows = await _revisions(db, notebook_id, after=after, limit=page)
        out += rows
        if len(rows) < page:
            return out
        after = rows[-1].revision


@dataclass(slots=True)
class FactTx:
    """One notebook's facts inside one locked transaction."""

    db: AsyncSession
    notebook: NotebookRow
    author: str | None
    embedder: Embedder
    at: int = field(default_factory=now_ms)
    changed: bool = False

    async def history(self) -> list[Revision]:
        """Every change so far, read under this transaction's lock."""
        return await _history(self.db, self.notebook.id, page=5_000)

    async def current(self) -> list[Fact]:
        rows = await self.db.scalars(
            select(FactRow)
            .where(FactRow.notebook_id == self.notebook.id, FactRow.deleted_at.is_(None))
            .order_by(FactRow.id)
        )
        return [_fact(r) for r in rows]

    async def insert(
        self,
        text: str,
        provenance: Provenance,
        *,
        created_at: int | None = None,
        expires_on: str | None = None,
        typed: Typed | None = None,
    ) -> str:
        row = FactRow(
            notebook_id=self.notebook.id,
            text=text,
            author=self.author,
            created_at=created_at or self.at,
            updated_at=self.at,
            expires_on=expires_on,
            **_columns(provenance),
            **_typed_columns(typed),
        )
        self.db.add(row)
        await self.db.flush()
        self._log(row.id, "insert", text, provenance)
        await self._index(row.id, text, provenance)
        return str(row.id)

    async def update(self, fact_id: str, text: str, provenance: Provenance) -> None:
        await self.db.execute(
            update(FactRow)
            .where(FactRow.notebook_id == self.notebook.id, FactRow.id == int(fact_id))
            .values(text=text, updated_at=self.at, **_columns(provenance))
        )
        self._log(int(fact_id), "update", text, provenance)
        await self._index(int(fact_id), text, provenance)

    async def set_typed(self, fact: Fact, typed: Typed) -> None:
        """Give a plain-text fact the type an observation repeating it carried. Not a
        change to what the fact says, so no revision."""
        await self.db.execute(
            update(FactRow)
            .where(FactRow.notebook_id == self.notebook.id, FactRow.id == int(fact.id))
            .values(**_typed_columns(typed))
        )

    async def remove(self, facts: Sequence[Fact]) -> None:
        if not facts:
            return
        await self.db.execute(
            update(FactRow)
            .where(
                FactRow.notebook_id == self.notebook.id,
                FactRow.id.in_([int(f.id) for f in facts]),
                FactRow.deleted_at.is_(None),
            )
            .values(deleted_at=self.at)
        )
        for fact in facts:
            self._log(int(fact.id), "delete", fact.text, fact.provenance)
            await self._unindex(int(fact.id))

    async def revive(self, fact_id: str, text: str, provenance: Provenance) -> None:
        """Bring a deleted fact back under its own id (restore keeps identity)."""
        await self.db.execute(
            update(FactRow)
            .where(FactRow.notebook_id == self.notebook.id, FactRow.id == int(fact_id))
            .values(text=text, deleted_at=None, updated_at=self.at, **_columns(provenance))
        )
        self._log(int(fact_id), "restore", text, provenance)
        await self._index(int(fact_id), text, provenance)

    def _log(self, fact_id: int, op: str, text: str, provenance: Provenance) -> None:
        if not self.changed:
            self.changed = True
            self.notebook.revision = int(self.notebook.revision) + 1
        self.db.add(
            FactRevisionRow(
                notebook_id=self.notebook.id,
                revision=self.notebook.revision,
                fact_id=fact_id,
                op=op,
                text=text,
                author=self.author,
                at=self.at,
                **_columns(provenance),
            )
        )

    # --- the semantic bank ------------------------------------------------------------

    async def reindex(self) -> None:
        """Make every live fact's bank item what ``bankable`` now says (after an archive
        or unarchive). Not a change to the facts: no revision."""
        for fact in await self.current():
            await self._index(int(fact.id), fact.text, fact.provenance)

    async def _unindex(self, fact_id: int) -> None:
        link = await self.db.get(FactItemRow, fact_id)
        if link is None:
            return
        await self.db.execute(
            update(MemoryItemRow).where(MemoryItemRow.id == link.item_id).values(forgotten=1)
        )
        await self.db.delete(link)

    async def set_expiry(self, fact: Fact, expires_on: str | None) -> None:
        """Make ``fact`` time-bound (or not); a change to the fact, so it is logged."""
        await self.db.execute(
            update(FactRow)
            .where(FactRow.notebook_id == self.notebook.id, FactRow.id == int(fact.id))
            .values(expires_on=expires_on, updated_at=self.at)
        )
        self._log(int(fact.id), "update", fact.text, fact.provenance)
        await self._index(int(fact.id), fact.text, fact.provenance)

    async def _index(self, fact_id: int, text: str, provenance: Provenance) -> None:
        """The fact's bank item, replacing the one it had (if it may be indexed at all)."""
        await self._unindex(fact_id)
        expires_on = await self.db.scalar(select(FactRow.expires_on).where(FactRow.id == fact_id))
        if not bankable(self.notebook, provenance, expires_on):
            return
        item_id = str(uuid.uuid4())
        self.db.add(
            MemoryItemRow(
                id=item_id,
                tenant=self.notebook.tenant_id,
                subject=subject_of(self.notebook.owner_principal_id),
                namespace=self.notebook.agent or self.notebook.agent_pin,
                content=text,
                embedding=self.embedder.embed(text),
                source_session=None,
                source_seq=None,
                confidence=BANK_CONFIDENCE,
                superseded_by=None,
                valid_from_ms=self.at,
                created_at_ms=self.at,
                content_hash=content_hash(text),
                forgotten=0,
            )
        )
        await self.db.flush()
        self.db.add(FactItemRow(fact_id=fact_id, notebook_id=self.notebook.id, item_id=item_id))


class NotebookStore:
    """Notebook rows plus locked fact transactions."""

    def __init__(
        self, factory: async_sessionmaker[AsyncSession], embedder: Embedder | None = None
    ) -> None:
        self._db = factory
        self._embedder = embedder or CachingEmbedder()

    # --- notebooks -------------------------------------------------------------

    async def create(
        self,
        *,
        tenant_id: str,
        owner: str,
        agent_pin: str,
        name: str,
        label: str | None = None,
        agent: str | None = None,
    ) -> Notebook:
        async with self._db() as db:
            if await self._find(db, tenant_id, owner, agent_pin, name) is not None:
                raise NotebookExists(name)
            row = NotebookRow(
                id=notebook_id(tenant_id),
                tenant_id=tenant_id,
                owner_principal_id=owner,
                agent_pin=agent_pin,
                agent=agent,
                name=name,
                label=label,
                revision=0,
                created_at=now_ms(),
            )
            db.add(row)
            await db.commit()
            return _notebook(row)

    async def ensure(
        self, *, tenant_id: str, owner: str, agent_pin: str, name: str, agent: str | None = None
    ) -> Notebook:
        """The named notebook, created on first use (safe under concurrent callers)."""
        async with self._db() as db:
            if (row := await self._find(db, tenant_id, owner, agent_pin, name)) is not None:
                if agent and row.agent is None:  # learnt later: index future facts under it
                    row.agent = agent
                    await db.commit()
                return _notebook(row)
        try:
            return await self.create(
                tenant_id=tenant_id, owner=owner, agent_pin=agent_pin, name=name, agent=agent
            )
        except (NotebookExists, IntegrityError):  # lost a create race: use the winner's row
            async with self._db() as db:
                if (row := await self._find(db, tenant_id, owner, agent_pin, name)) is None:
                    raise
                return _notebook(row)

    async def get(self, notebook_id: str) -> Notebook:
        async with self._db() as db:
            row = await db.get(NotebookRow, notebook_id)
            if row is None:
                raise NotebookNotFound(notebook_id)
            return _notebook(row)

    async def list_notebooks(
        self, *, tenant_id: str, owner: str, agent_pin: str, archived: bool = False
    ) -> list[Notebook]:
        query = select(NotebookRow).where(
            NotebookRow.tenant_id == tenant_id,
            NotebookRow.owner_principal_id == owner,
            NotebookRow.agent_pin == agent_pin,
        )
        if not archived:
            query = query.where(NotebookRow.archived_at.is_(None))
        async with self._db() as db:
            return [_notebook(r) for r in await db.scalars(query.order_by(NotebookRow.created_at))]

    async def set_archived(self, notebook_id: str, archived: bool) -> Notebook:
        """Archive (read-only, out of bank recall) or unarchive (its facts indexed again)."""
        async with self._db() as db:
            row = await self._lock(db, notebook_id)
            if (row.archived_at is not None) != archived:
                row.archived_at = now_ms() if archived else None
                await FactTx(db, row, None, self._embedder).reindex()
            await db.commit()
            return _notebook(row)

    @staticmethod
    async def _lock(db: AsyncSession, notebook_id: str) -> NotebookRow:
        """The notebook row, locked for this transaction by a no-op write (portable)."""
        locked = await db.scalar(
            update(NotebookRow)
            .where(NotebookRow.id == notebook_id)
            .values(revision=NotebookRow.revision)
            .returning(NotebookRow.id)
        )
        if locked is None:
            raise NotebookNotFound(notebook_id)
        notebook = await db.get(NotebookRow, notebook_id, populate_existing=True)
        assert notebook is not None
        return notebook

    async def mark_tidied(self, notebook_id: str, at: int) -> None:
        async with self._db() as db:
            await db.execute(
                update(NotebookRow).where(NotebookRow.id == notebook_id).values(last_tidied_at=at)
            )
            await db.commit()

    # --- facts -----------------------------------------------------------------

    async def facts(self, notebook_id: str) -> list[Fact]:
        async with self._db() as db:
            rows = await db.scalars(
                select(FactRow)
                .where(FactRow.notebook_id == notebook_id, FactRow.deleted_at.is_(None))
                .order_by(FactRow.id)
            )
            return [_fact(r) for r in rows]

    async def transact[T](
        self,
        notebook_id: str,
        fn: Callable[[FactTx], Awaitable[T]],
        *,
        author: str | None = None,
        expect_revision: int | None = None,
    ) -> T:
        """Run ``fn`` on the notebook's facts atomically; ``expect_revision`` refuses stale."""
        async with self._db() as db:
            # Lock by writing: portable, and taken before any read the change depends on.
            notebook = await self._lock(db, notebook_id)
            if notebook.archived_at is not None:
                raise NotebookArchived(notebook_id)
            if expect_revision is not None and int(notebook.revision) != expect_revision:
                raise RevisionConflict(expect_revision, int(notebook.revision))
            tx = FactTx(db, notebook, author, self._embedder)
            result = await fn(tx)
            await db.commit()
            return result

    async def revisions(
        self, notebook_id: str, *, after: int = 0, limit: int = 500
    ) -> list[Revision]:
        """The changes after revision ``after``: about ``limit`` rows, in whole revisions.

        One revision can change many facts (a row each). A page never ends inside one, so
        the next page is ``after=<last revision of this one>`` and nothing is skipped."""
        async with self._db() as db:
            return await _revisions(db, notebook_id, after=after, limit=limit)

    async def history(self, notebook_id: str, *, page: int = 5_000) -> list[Revision]:
        """Every change to the notebook, oldest first (read in pages, never cut off)."""
        async with self._db() as db:
            return await _history(db, notebook_id, page=page)

    async def stats(self, notebook_id: str) -> NotebookStats:
        async with self._db() as db:
            count, size, at = (
                await db.execute(
                    select(
                        func.count(FactRow.id),
                        func.coalesce(func.sum(func.length(FactRow.text)), 0),
                        func.max(FactRow.updated_at),
                    ).where(FactRow.notebook_id == notebook_id, FactRow.deleted_at.is_(None))
                )
            ).one()
            return NotebookStats(int(count), int(size), None if at is None else int(at))

    # --- erasure ---------------------------------------------------------------------

    # --- the inbox (G2: automatic captures, appended without the notebook's lock) -------

    async def append_inbox(
        self,
        notebook_id: str,
        observations: Sequence[dict[str, object]],
        provenance: Provenance,
        *,
        author: str | None,
        expires_on: str | None,
        retire: Sequence[str] = (),
        idempotency_key: str | None,
    ) -> bool:
        """Queue one capture's observations; False when ``idempotency_key`` is queued
        already (a retried burst). One insert, no lock on the notebook: captures into one
        notebook never wait on each other, nor on its edits."""
        async with self._db() as db:
            db.add(
                InboxRow(
                    notebook_id=notebook_id,
                    idempotency_key=idempotency_key,
                    observations=list(observations),
                    retire=list(retire),
                    author=author,
                    expires_on=expires_on,
                    received_at=now_ms(),
                    **_columns(provenance),
                )
            )
            try:
                await db.commit()
            except IntegrityError:
                await db.rollback()
                if idempotency_key is not None and await db.scalar(
                    select(InboxRow.id).where(
                        InboxRow.notebook_id == notebook_id, InboxRow.idempotency_key == idempotency_key
                    )
                ):
                    return False  # queued already: a retried burst
                raise NotebookNotFound(notebook_id) from None  # erased under it: nothing queued
            return True

    async def pending_notebooks(self, *, limit: int) -> list[str]:
        """Notebooks with queued captures, the longest waiting first."""
        async with self._db() as db:
            rows = await db.execute(
                select(InboxRow.notebook_id)
                .where(InboxRow.promoted_at.is_(None))
                .group_by(InboxRow.notebook_id)
                .order_by(func.min(InboxRow.id))
                .limit(limit)
            )
            return [str(r[0]) for r in rows]

    async def pending(self) -> int:
        """Captures queued, in every notebook."""
        async with self._db() as db:
            return int(
                await db.scalar(select(func.count()).select_from(InboxRow).where(InboxRow.promoted_at.is_(None))) or 0
            )

    async def drop_inbox(self, notebook_id: str) -> int:
        """Discard a notebook's queued captures (it was archived after they were queued)."""
        async with self._db() as db:
            dropped = await db.execute(
                delete(InboxRow).where(InboxRow.notebook_id == notebook_id, InboxRow.promoted_at.is_(None))
            )
            await db.commit()
            return int(dropped.rowcount or 0)  # type: ignore[attr-defined]

    async def erase_owner(self, *, tenant_id: str, owner: str) -> int:
        """Delete every notebook of ``owner`` with its facts, history and bank items.

        Erasure, unlike ``clear``: nothing is kept, the revision log included.
        Returns the number of notebooks removed.
        """
        async with self._db() as db:
            ids = list(
                await db.scalars(
                    select(NotebookRow.id).where(
                        NotebookRow.tenant_id == tenant_id, NotebookRow.owner_principal_id == owner
                    )
                )
            )
            if not ids:
                return 0
            items = list(
                await db.scalars(select(FactItemRow.item_id).where(FactItemRow.notebook_id.in_(ids)))
            )
            await db.execute(delete(FactItemRow).where(FactItemRow.notebook_id.in_(ids)))
            if items:
                await db.execute(delete(MemoryItemRow).where(MemoryItemRow.id.in_(items)))
            await db.execute(delete(FactRevisionRow).where(FactRevisionRow.notebook_id.in_(ids)))
            await db.execute(delete(InboxRow).where(InboxRow.notebook_id.in_(ids)))  # not yet facts, theirs all the same
            await db.execute(delete(FactRow).where(FactRow.notebook_id.in_(ids)))
            await db.execute(delete(NotebookRow).where(NotebookRow.id.in_(ids)))
            await db.commit()
            return len(ids)

    @staticmethod
    async def _find(
        db: AsyncSession, tenant_id: str, owner: str, agent_pin: str, name: str
    ) -> NotebookRow | None:
        return await db.scalar(
            select(NotebookRow).where(
                NotebookRow.tenant_id == tenant_id,
                NotebookRow.owner_principal_id == owner,
                NotebookRow.agent_pin == agent_pin,
                NotebookRow.name == name,
            )
        )
