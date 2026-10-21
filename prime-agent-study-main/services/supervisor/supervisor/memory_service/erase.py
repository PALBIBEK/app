"""Audited forget + subject right-to-erasure cascade."""

from __future__ import annotations

import time
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from supervisor.memory_service.metrics import METRICS
from supervisor.memory_service.models import ForgetAuditRow, MemoryItemRow, MemorySessionRow
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
        Right-to-erasure for a subject within a tenant.
        Scoped query — does not scan other tenants' rows.
        """
        require_tenant(tenant)
        if not subject:
            raise ValueError("subject required")
        if not actor or not reason:
            raise ValueError("actor and reason required")
        async with self._factory() as db:
            items = list(
                (
                    await db.execute(
                        select(MemoryItemRow).where(
                            MemoryItemRow.tenant == tenant,
                            MemoryItemRow.subject == subject,
                            MemoryItemRow.forgotten == 0,
                        )
                    )
                )
                .scalars()
                .all()
            )
            item_ids = [r.id for r in items]
            for r in items:
                r.forgotten = 1

            sessions = list(
                (
                    await db.execute(
                        select(MemorySessionRow).where(
                            MemorySessionRow.tenant == tenant,
                            MemorySessionRow.subject == subject,
                        )
                    )
                )
                .scalars()
                .all()
            )
            for s in sessions:
                s.status = "erased"

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
            await db.commit()
        METRICS.inc("bank_forgets", len(item_ids))
        return {"items_forgotten": len(item_ids), "sessions_marked": len(sessions)}

