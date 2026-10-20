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
    NotebookExists,
    NotebookNotFound,
    RevisionConflict,
)
from supervisor.memory_service.notebooks.models import (
    FactItemRow,
    FactRevisionRow,
    FactRow,
    NotebookRow,
)
from supervisor.memory_service.notebooks.types import notebook_id, (
    Fact,
    Notebook,
    NotebookStats,
    Provenance,
    Revision,
    Sensitivity,
    personal_source,
)

BANK_CONFIDENCE = 0.9
"""A fact a user or agent wrote down is surer than one extracted from a transcript."""


def now_ms() -> int:
    return int(time.time() * 1000)


def subject_of(owner: str) -> str:
    """The bank subject of an owner's facts (as ``remember`` and durable recall scope them)."""
    return f"user:{owner}"


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
    )


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


def bankable(notebook: NotebookRow, provenance: Provenance) -> bool:
    """Whether a fact may sit in the bank, whose recall applies no audience rules."""
    home = personal_source(notebook.owner_principal_id)
    return (
        provenance.sensitivity is Sensitivity.ORDINARY
        and not provenance.source_unknown
        and set(provenance.sources) <= {home}
    )


@dataclass(slots=True)
class FactTx:
    """One notebook's facts inside one locked transaction."""

    db: AsyncSession
    notebook: NotebookRow
    author: str | None
    embedder: Embedder
    at: int = field(default_factory=now_ms)
    changed: bool = False

    async def current(self) -> list[Fact]:
        rows = await self.db.scalars(
            select(FactRow)
            .where(FactRow.notebook_id == self.notebook.id, FactRow.deleted_at.is_(None))
            .order_by(FactRow.id)
        )
        return [_fact(r) for r in rows]

    async def insert(
        self, text: str, provenance: Provenance, *, created_at: int | None = None
    ) -> str:
        row = FactRow(
            notebook_id=self.notebook.id,
            text=text,
            author=self.author,
            created_at=created_at or self.at,
            updated_at=self.at,
            **_columns(provenance),
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

    async def _unindex(self, fact_id: int) -> None:
        link = await self.db.get(FactItemRow, fact_id)
        if link is None:
            return
        await self.db.execute(
            update(MemoryItemRow).where(MemoryItemRow.id == link.item_id).values(forgotten=1)
        )
        await self.db.delete(link)

    async def _index(self, fact_id: int, text: str, provenance: Provenance) -> None:
        """The fact's bank item, replacing the one it had (if it may be indexed at all)."""
        await self._unindex(fact_id)
        if not bankable(self.notebook, provenance):
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
        async with self._db() as db:
            row = await db.get(NotebookRow, notebook_id)
            if row is None:
                raise NotebookNotFound(notebook_id)
            row.archived_at = now_ms() if archived else None
            await db.commit()
            return _notebook(row)

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
            if expect_revision is not None and int(notebook.revision) != expect_revision:
                raise RevisionConflict(expect_revision, int(notebook.revision))
            tx = FactTx(db, notebook, author, self._embedder)
            result = await fn(tx)
            await db.commit()
            return result

    async def revisions(
        self, notebook_id: str, *, after: int = 0, limit: int = 500
    ) -> list[Revision]:
        async with self._db() as db:
            rows = await db.scalars(
                select(FactRevisionRow)
                .where(FactRevisionRow.notebook_id == notebook_id, FactRevisionRow.revision > after)
                .order_by(FactRevisionRow.revision, FactRevisionRow.id)
                .limit(limit)
            )
            return [_revision(r) for r in rows]

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
