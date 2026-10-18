"""Tier 2 memory bank — write / supersede / get / forget (no in-place update)."""

from __future__ import annotations

import time
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from memory_service.embed import Embedder, content_hash
from memory_service.cost import CachingEmbedder
from memory_service.metrics import METRICS
from memory_service.models import MemoryItemRow
from memory_service.tenant import require_scope
from memory_service.tier2_types import ForgetSelector, ItemNotFound, MemoryItem, NewMemoryItem
from memory_service.types import Scope


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
        now = int(time.time() * 1000)
        item_id = str(uuid.uuid4())
        emb = self._embedder.embed(item.content)
        async with self._factory() as db:
            db.add(
                MemoryItemRow(
                    id=item_id,
                    tenant=scope.tenant,
                    subject=scope.subject,
                    namespace=scope.namespace,
                    content=item.content,
                    embedding=emb,
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
            await db.commit()
        METRICS.inc("bank_writes")
        return item_id

    async def supersede(self, old_id: str, replacement: NewMemoryItem, scope: Scope) -> str:
        require_scope(scope)
        new_id = await self.write(scope, replacement)
        async with self._factory() as db:
            old = await db.get(MemoryItemRow, old_id)
            if old is None or old.forgotten:
                raise ItemNotFound(f"ITEM_NOT_FOUND: {old_id}")
            old.superseded_by = new_id
            await db.commit()
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

    async def wipe_scope(self, scope: Scope) -> int:
        """Test/rebuild helper — hard-delete all items in scope."""
        async with self._factory() as db:
            result = await db.execute(
                select(MemoryItemRow).where(
                    MemoryItemRow.tenant == scope.tenant,
                    MemoryItemRow.subject == scope.subject,
                    MemoryItemRow.namespace == scope.namespace,
                )
            )
            rows = list(result.scalars().all())
            for r in rows:
                await db.delete(r)
            await db.commit()
            return len(rows)

    async def find_near_duplicate(
        self, scope: Scope, content: str, *, threshold: float = 0.92
    ) -> MemoryItemRow | None:
        from memory_service.embed import cosine

        emb = self._embedder.embed(content)
        ch = content_hash(content)
        actives = await self.list_active(scope)
        for row in actives:
            if row.content_hash == ch:
                return row
            if cosine(emb, list(row.embedding or [])) >= threshold:
                return row
        return None
