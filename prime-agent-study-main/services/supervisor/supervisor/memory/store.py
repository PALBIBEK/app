"""SQL storage for notebooks and facts.

Every change runs in one transaction that first writes the notebook row (a no-op
``revision = revision``): a row lock on Postgres, the database write lock on
SQLite, so concurrent writers on any worker serialise before they read. It then
bumps the notebook's revision once if anything changed, and appends one revision
row per fact change. The revision
log is what history, restore and consolidation's stale-write check stand on.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from supervisor.db.models import MemoryFactRevisionRow, MemoryFactRow, MemoryNotebookRow
from supervisor.memory_service.notebooks.errors import (
    NotebookExists,
    NotebookNotFound,
    RevisionConflict,
)
from supervisor.memory_service.notebooks.types import (
    Fact,
    Notebook,
    NotebookStats,
    Provenance,
    Revision,
    Sensitivity,
)


def now_ms() -> int:
    return int(time.time() * 1000)


def _provenance(row: MemoryFactRow | MemoryFactRevisionRow) -> Provenance:
    return Provenance(
        Sensitivity.parse(row.sensitivity), tuple(row.sources or ()), bool(row.source_unknown)
    )


def _fact(row: MemoryFactRow) -> Fact:
    return Fact(
        id=str(row.id),
        text=row.text,
        provenance=_provenance(row),
        created_at=int(row.created_at),
        updated_at=int(row.updated_at),
        author=row.author,
    )


def _notebook(row: MemoryNotebookRow) -> Notebook:
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
    )


def _revision(row: MemoryFactRevisionRow) -> Revision:
    return Revision(
        revision=int(row.revision),
        fact_id=str(row.fact_id),
        op=row.op,
        text=row.text,
        provenance=_provenance(row),
        at=int(row.at),
        author=row.author,
    )


@dataclass(slots=True)
class FactTx:
    """One notebook's facts inside one locked transaction (QM's FactStoreTx)."""

    db: AsyncSession
    notebook: MemoryNotebookRow
    author: str | None
    at: int = field(default_factory=now_ms)
    changed: bool = False

    async def current(self) -> list[Fact]:
        rows = await self.db.scalars(
            select(MemoryFactRow)
            .where(
                MemoryFactRow.notebook_id == self.notebook.id, MemoryFactRow.deleted_at.is_(None)
            )
            .order_by(MemoryFactRow.id)
        )
        return [_fact(r) for r in rows]

    async def insert(
        self, text: str, provenance: Provenance, *, created_at: int | None = None
    ) -> str:
        row = MemoryFactRow(
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
        return str(row.id)

    async def update(self, fact_id: str, text: str, provenance: Provenance) -> None:
        await self.db.execute(
            update(MemoryFactRow)
            .where(MemoryFactRow.notebook_id == self.notebook.id, MemoryFactRow.id == int(fact_id))
            .values(text=text, updated_at=self.at, **_columns(provenance))
        )
        self._log(int(fact_id), "update", text, provenance)

    async def remove(self, facts: Sequence[Fact]) -> None:
        if not facts:
            return
        await self.db.execute(
            update(MemoryFactRow)
            .where(
                MemoryFactRow.notebook_id == self.notebook.id,
                MemoryFactRow.id.in_([int(f.id) for f in facts]),
                MemoryFactRow.deleted_at.is_(None),
            )
            .values(deleted_at=self.at)
        )
        for fact in facts:
            self._log(int(fact.id), "delete", fact.text, fact.provenance)

    async def revive(self, fact_id: str, text: str, provenance: Provenance) -> None:
        """Bring a deleted fact back under its own id (restore keeps identity)."""
        await self.db.execute(
            update(MemoryFactRow)
            .where(MemoryFactRow.notebook_id == self.notebook.id, MemoryFactRow.id == int(fact_id))
            .values(text=text, deleted_at=None, updated_at=self.at, **_columns(provenance))
        )
        self._log(int(fact_id), "restore", text, provenance)

    def _log(self, fact_id: int, op: str, text: str, provenance: Provenance) -> None:
        if not self.changed:
            self.changed = True
            self.notebook.revision = int(self.notebook.revision) + 1
        self.db.add(
            MemoryFactRevisionRow(
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


def _columns(provenance: Provenance) -> dict[str, object]:
    return {
        "sensitivity": provenance.sensitivity.value,
        "sources": list(provenance.sources),
        "source_unknown": provenance.source_unknown,
    }


class NotebookStore:
    """Notebook rows plus locked fact transactions."""

    def __init__(self, factory: async_sessionmaker[AsyncSession]) -> None:
        self._db = factory

    # --- notebooks -------------------------------------------------------------

    async def create(
        self, *, tenant_id: str, owner: str, agent_pin: str, name: str, label: str | None = None
    ) -> Notebook:
        async with self._db() as db:
            if await self._find(db, tenant_id, owner, agent_pin, name) is not None:
                raise NotebookExists(name)
            row = MemoryNotebookRow(
                id=uuid.uuid4().hex,
                tenant_id=tenant_id,
                owner_principal_id=owner,
                agent_pin=agent_pin,
                name=name,
                label=label,
                revision=0,
                created_at=now_ms(),
            )
            db.add(row)
            await db.commit()
            return _notebook(row)

    async def ensure(self, *, tenant_id: str, owner: str, agent_pin: str, name: str) -> Notebook:
        """The named notebook, created on first use (safe under concurrent callers)."""
        async with self._db() as db:
            if (row := await self._find(db, tenant_id, owner, agent_pin, name)) is not None:
                return _notebook(row)
        try:
            return await self.create(
                tenant_id=tenant_id, owner=owner, agent_pin=agent_pin, name=name
            )
        except (NotebookExists, IntegrityError):  # lost a create race: use the winner's row
            async with self._db() as db:
                if (row := await self._find(db, tenant_id, owner, agent_pin, name)) is None:
                    raise
                return _notebook(row)

    async def get(self, notebook_id: str) -> Notebook:
        async with self._db() as db:
            row = await db.get(MemoryNotebookRow, notebook_id)
            if row is None:
                raise NotebookNotFound(notebook_id)
            return _notebook(row)

    async def list_notebooks(
        self, *, tenant_id: str, owner: str, agent_pin: str, archived: bool = False
    ) -> list[Notebook]:
        query = select(MemoryNotebookRow).where(
            MemoryNotebookRow.tenant_id == tenant_id,
            MemoryNotebookRow.owner_principal_id == owner,
            MemoryNotebookRow.agent_pin == agent_pin,
        )
        if not archived:
            query = query.where(MemoryNotebookRow.archived_at.is_(None))
        async with self._db() as db:
            return [
                _notebook(r) for r in await db.scalars(query.order_by(MemoryNotebookRow.created_at))
            ]

    async def set_archived(self, notebook_id: str, archived: bool) -> Notebook:
        async with self._db() as db:
            row = await db.get(MemoryNotebookRow, notebook_id)
            if row is None:
                raise NotebookNotFound(notebook_id)
            row.archived_at = now_ms() if archived else None
            await db.commit()
            return _notebook(row)

    async def mark_tidied(self, notebook_id: str, at: int) -> None:
        async with self._db() as db:
            await db.execute(
                update(MemoryNotebookRow)
                .where(MemoryNotebookRow.id == notebook_id)
                .values(last_tidied_at=at)
            )
            await db.commit()

    # --- facts -----------------------------------------------------------------

    async def facts(self, notebook_id: str) -> list[Fact]:
        async with self._db() as db:
            rows = await db.scalars(
                select(MemoryFactRow)
                .where(MemoryFactRow.notebook_id == notebook_id, MemoryFactRow.deleted_at.is_(None))
                .order_by(MemoryFactRow.id)
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
                update(MemoryNotebookRow)
                .where(MemoryNotebookRow.id == notebook_id)
                .values(revision=MemoryNotebookRow.revision)
                .returning(MemoryNotebookRow.id)
            )
            if locked is None:
                raise NotebookNotFound(notebook_id)
            notebook = await db.get(MemoryNotebookRow, notebook_id, populate_existing=True)
            assert notebook is not None
            if expect_revision is not None and int(notebook.revision) != expect_revision:
                raise RevisionConflict(expect_revision, int(notebook.revision))
            tx = FactTx(db, notebook, author)
            result = await fn(tx)
            await db.commit()
            return result

    async def revisions(
        self, notebook_id: str, *, after: int = 0, limit: int = 500
    ) -> list[Revision]:
        async with self._db() as db:
            rows = await db.scalars(
                select(MemoryFactRevisionRow)
                .where(
                    MemoryFactRevisionRow.notebook_id == notebook_id,
                    MemoryFactRevisionRow.revision > after,
                )
                .order_by(MemoryFactRevisionRow.revision, MemoryFactRevisionRow.id)
                .limit(limit)
            )
            return [_revision(r) for r in rows]

    async def stats(self, notebook_id: str) -> NotebookStats:
        async with self._db() as db:
            count, size, at = (
                await db.execute(
                    select(
                        func.count(MemoryFactRow.id),
                        func.coalesce(func.sum(func.length(MemoryFactRow.text)), 0),
                        func.max(MemoryFactRow.updated_at),
                    ).where(
                        MemoryFactRow.notebook_id == notebook_id, MemoryFactRow.deleted_at.is_(None)
                    )
                )
            ).one()
            return NotebookStats(int(count), int(size), None if at is None else int(at))

    @staticmethod
    async def _find(
        db: AsyncSession, tenant_id: str, owner: str, agent_pin: str, name: str
    ) -> MemoryNotebookRow | None:
        return await db.scalar(
            select(MemoryNotebookRow).where(
                MemoryNotebookRow.tenant_id == tenant_id,
                MemoryNotebookRow.owner_principal_id == owner,
                MemoryNotebookRow.agent_pin == agent_pin,
                MemoryNotebookRow.name == name,
            )
        )
