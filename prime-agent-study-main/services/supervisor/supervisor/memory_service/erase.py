"""Audited forget + subject right-to-erasure cascade."""

from __future__ import annotations

import time
import uuid

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from supervisor.memory_service.metrics import METRICS
from supervisor.memory_service.models import (
    ExtractionJobRow,
    ForgetAuditRow,
    MemoryItemRow,
    MemorySessionRow,
    OutboxRow,
    SessionArchiveRow,
    SessionEventRow,
)
from supervisor.memory_service.tenant import require_tenant
from supervisor.memory_service.tier2_types import ForgetSelector
from supervisor.memory_service.types import Scope


class EraseService:
    def __init__(self, factory: async_sessionmaker[AsyncSession]) -> None:
        self._factory = factory

    async def forget(
        self,
        scope: Scope,
        selector: ForgetSelector,
        *,
        actor: str,
        reason: str,
    ) -> int:
        require_tenant(scope.tenant)
        if not actor or not reason:
            raise ValueError("actor and reason required for audited forget")
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
            ids = [r.id for r in rows]
            for r in rows:
                r.forgotten = 1
            db.add(
                ForgetAuditRow(
                    id=str(uuid.uuid4()),
                    tenant=scope.tenant,
                    subject=scope.subject,
                    namespace=scope.namespace,
                    actor=actor,
                    reason=reason,
                    item_ids=ids,
                    count=len(ids),
                    created_at_ms=int(time.time() * 1000),
                    kind="forget",
                )
            )
            await db.commit()
        METRICS.inc("bank_forgets", len(ids))
        return len(ids)

    async def erase_subject(
        self,
        *,
        tenant: str,
        subject: str,
        actor: str,
        reason: str,
    ) -> dict[str, int]:
        """
        Right-to-erasure for a subject within a tenant: their bank items are deleted
        (forgotten ones included: forgetting only hides an item from recall), and their
        sessions' events, archives, outbox and extraction jobs are deleted. A session's
        header stays, marked erased, so loading it says it is gone. The audit row keeps
        ids and counts, no content. Scoped query: does not scan other tenants' rows.
        """
        require_tenant(tenant)
        if not subject:
            raise ValueError("subject required")
        if not actor or not reason:
            raise ValueError("actor and reason required")
        async with self._factory() as db, db.begin():
            items = MemoryItemRow.tenant == tenant, MemoryItemRow.subject == subject
            item_ids = list(await db.scalars(select(MemoryItemRow.id).where(*items)))
            # Successors point at predecessors' rows: clear the links, then delete.
            await db.execute(update(MemoryItemRow).where(*items).values(superseded_by=None))
            await db.execute(delete(MemoryItemRow).where(*items))

            sessions = list(
                await db.scalars(
                    select(MemorySessionRow.id).where(
                        MemorySessionRow.tenant == tenant, MemorySessionRow.subject == subject
                    )
                )
            )
            events = 0
            for chunk in (sessions[i : i + 500] for i in range(0, len(sessions), 500)):
                for table in (SessionEventRow, OutboxRow, ExtractionJobRow, SessionArchiveRow):
                    gone = await db.execute(delete(table).where(table.session_id.in_(chunk)))
                    if table is SessionEventRow:
                        events += gone.rowcount or 0  # type: ignore[attr-defined]
                await db.execute(
                    update(MemorySessionRow).where(MemorySessionRow.id.in_(chunk)).values(status="erased")
                )

            db.add(
                ForgetAuditRow(
                    id=str(uuid.uuid4()),
                    tenant=tenant,
                    subject=subject,
                    namespace="*",
                    actor=actor,
                    reason=reason,
                    item_ids=item_ids,
                    count=len(item_ids),
                    created_at_ms=int(time.time() * 1000),
                    kind="erase_subject",
                )
            )
        METRICS.inc("bank_forgets", len(item_ids))
        return {"items_forgotten": len(item_ids), "sessions_marked": len(sessions), "events_deleted": events}
