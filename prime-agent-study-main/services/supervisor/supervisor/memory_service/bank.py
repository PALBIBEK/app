"""Tier 2 memory bank — write / supersede / get / forget (no in-place update)."""

from __future__ import annotations

import time
import uuid

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from supervisor.memory_service import vector_search
from supervisor.memory_service.cost import CachingEmbedder
from supervisor.memory_service.embed import Embedder, content_hash
from supervisor.memory_service.metrics import METRICS
from supervisor.memory_service.models import MemoryItemRow
from supervisor.memory_service.tenant import require_scope
from supervisor.memory_service.tier2_types import (
    ForgetSelector,
    ItemNotFound,
    ItemSuperseded,
    MemoryItem,
    NewMemoryItem,
)
from supervisor.memory_service.types import Scope


def _row_to_item(row: MemoryItemRow) -> MemoryItem:
    return MemoryItem(
        id=row.id,
        scope=Scope(tenant=row.tenant, subject=row.subject, namespace=row.namespace),
        content=row.content,
        confidence=float(row.confidence),
        source_session=row.source_session,
        source_seq=row.source_seq,
        superseded_by=row.superseded_by,
        valid_from_ms=row.valid_from_ms,
        created_at_ms=row.created_at_ms,
    )


class MemoryBank:
    def __init__(
        self,
        factory: async_sessionmaker[AsyncSession],
        embedder: Embedder | None = None,
    ) -> None:
        self._factory = factory
        self._embedder = embedder or CachingEmbedder()

    async def write(self, scope: Scope, item: NewMemoryItem) -> str:
        require_scope(scope)
        async with self._factory() as db:
            item_id = self._add(db, scope, item)
            await db.commit()
        METRICS.inc("bank_writes")
        return item_id

    def _add(self, db: AsyncSession, scope: Scope, item: NewMemoryItem) -> str:
        now = int(time.time() * 1000)
        item_id = str(uuid.uuid4())
        db.add(
            MemoryItemRow(
                id=item_id,
                tenant=scope.tenant,
                subject=scope.subject,
                namespace=scope.namespace,
                content=item.content,
                embedding=self._embedder.embed(item.content),
                source_session=item.source_session,
                source_seq=item.source_seq,
                confidence=item.confidence,
                superseded_by=None,
                valid_from_ms=now,
                created_at_ms=now,
                content_hash=content_hash(item.content),
                forgotten=0,
            )
        )
        return item_id

    async def supersede(self, old_id: str, replacement: NewMemoryItem, scope: Scope) -> str:
        """Write ``replacement`` as the successor of ``old_id``, in one transaction.

        The old item must exist in ``scope``, not be forgotten, and be current: it is
        marked by a conditional UPDATE, so of two supersedes of one item one wins and the
        other raises ``ItemSuperseded`` (lineage stays a line). Nothing is written when it
        fails: no orphan replacement."""
        require_scope(scope)
        async with self._factory() as db, db.begin():
            new_id = self._add(db, scope, replacement)
            await db.flush()
            marked = await db.execute(
                update(MemoryItemRow)
                .where(
                    MemoryItemRow.id == old_id,
                    MemoryItemRow.tenant == scope.tenant,
                    MemoryItemRow.subject == scope.subject,
                    MemoryItemRow.namespace == scope.namespace,
                    MemoryItemRow.forgotten == 0,
                    MemoryItemRow.superseded_by.is_(None),
                )
                .values(superseded_by=new_id)
            )
            if int(getattr(marked, "rowcount", 0) or 0) != 1:
                old = await db.get(MemoryItemRow, old_id)
                in_scope = old is not None and (old.tenant, old.subject, old.namespace) == (
                    scope.tenant,
                    scope.subject,
                    scope.namespace,
                )
                if in_scope and old is not None and not old.forgotten and old.superseded_by:
                    raise ItemSuperseded(f"ITEM_SUPERSEDED: {old_id} by {old.superseded_by}")
                raise ItemNotFound(f"ITEM_NOT_FOUND: {old_id}")  # also another scope's: not disclosed
        METRICS.inc("bank_writes")
        METRICS.inc("bank_supersessions")
        return new_id

    async def get(self, item_id: str) -> MemoryItem | None:
        async with self._factory() as db:
            row = await db.get(MemoryItemRow, item_id)
            if row is None or row.forgotten:
                return None
            return _row_to_item(row)

    async def forget(self, scope: Scope, selector: ForgetSelector) -> int:
        async with self._factory() as db:
            q = select(MemoryItemRow).where(
                MemoryItemRow.tenant == scope.tenant,
                MemoryItemRow.subject == scope.subject,
                MemoryItemRow.namespace == scope.namespace,
                MemoryItemRow.forgotten == 0,
            )
            if selector.item_id:
                q = q.where(MemoryItemRow.id == selector.item_id)
            elif selector.source_session:
                q = q.where(MemoryItemRow.source_session == selector.source_session)
            elif not selector.all_in_scope:
                return 0
            rows = list((await db.execute(q)).scalars().all())
            for r in rows:
                r.forgotten = 1
            await db.commit()
            return len(rows)

    async def candidates(self, scope: Scope, query_vector: list[float]) -> list[MemoryItemRow]:
        """The items recall ranks: on Postgres with pgvector the nearest CANDIDATES of the
        scope (``vector_search``), elsewhere every live item."""
        async with self._factory() as db:
            if await vector_search.available(db):
                rows = await vector_search.nearest(db, scope, query_vector)
                for r in rows:
                    db.expunge(r)
                return rows
        return await self.list_active(scope)

    async def list_active(self, scope: Scope) -> list[MemoryItemRow]:
        async with self._factory() as db:
            result = await db.execute(
                select(MemoryItemRow).where(
                    MemoryItemRow.tenant == scope.tenant,
                    MemoryItemRow.subject == scope.subject,
                    MemoryItemRow.namespace == scope.namespace,
                    MemoryItemRow.superseded_by.is_(None),
                    MemoryItemRow.forgotten == 0,
                )
            )
            rows = list(result.scalars().all())
            for r in rows:
                db.expunge(r)
            return rows


    async def find_near_duplicate(
        self, scope: Scope, content: str, *, threshold: float = 0.92
    ) -> MemoryItemRow | None:
        from supervisor.memory_service.embed import cosine

        emb = self._embedder.embed(content)
        ch = content_hash(content)
        actives = await self.list_active(scope)
        for row in actives:
            if row.content_hash == ch:
                return row
            if cosine(emb, list(row.embedding or [])) >= threshold:
                return row
        return None
