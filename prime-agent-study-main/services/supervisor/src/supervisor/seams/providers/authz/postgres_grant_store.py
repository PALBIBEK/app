"""Postgres-backed GrantStore (Stage 1 / D11 durable grants)."""

from __future__ import annotations

from uuid import uuid4

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from supervisor.db.models import ScopeGrantRow, ScopeMembershipRow
from supervisor.seams.services.authz import ALL_GRANTS, Grant


class PostgresGrantStore:
    """GrantStore over ``scope_memberships`` / ``scope_grants``."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._db = session_factory

    async def is_member(self, principal_id: str, scope_id: str) -> bool:
        async with self._db() as db:
            row = await db.scalar(
                select(ScopeMembershipRow.id).where(
                    ScopeMembershipRow.scope_id == scope_id,
                    ScopeMembershipRow.principal_id == principal_id,
                )
            )
            return row is not None

    async def has_grant(
        self, principal_id: str, scope_id: str, grant: Grant
    ) -> bool:
        async with self._db() as db:
            row = await db.scalar(
                select(ScopeGrantRow.id).where(
                    ScopeGrantRow.scope_id == scope_id,
                    ScopeGrantRow.principal_id == principal_id,
                    ScopeGrantRow.grant == str(grant),
                )
            )
            return row is not None

    async def list_grants(
        self, principal_id: str, scope_id: str
    ) -> frozenset[Grant]:
        async with self._db() as db:
            rows = (
                await db.scalars(
                    select(ScopeGrantRow.grant).where(
                        ScopeGrantRow.scope_id == scope_id,
                        ScopeGrantRow.principal_id == principal_id,
                    )
                )
            ).all()
            out: set[Grant] = set()
            for raw in rows:
                try:
                    out.add(Grant(raw))
                except ValueError:
                    continue
            return frozenset(out)

    async def list_member_ids(self, scope_id: str) -> list[str]:
        async with self._db() as db:
            rows = (
                await db.scalars(
                    select(ScopeMembershipRow.principal_id).where(
                        ScopeMembershipRow.scope_id == scope_id
                    )
                )
            ).all()
            return sorted(str(pid) for pid in rows)

    async def add_member(self, principal_id: str, scope_id: str) -> None:
        async with self._db() as db:
            existing = await db.scalar(
                select(ScopeMembershipRow.id).where(
                    ScopeMembershipRow.scope_id == scope_id,
                    ScopeMembershipRow.principal_id == principal_id,
                )
            )
            if existing is None:
                db.add(
                    ScopeMembershipRow(
                        id=uuid4().hex,
                        scope_id=scope_id,
                        principal_id=principal_id,
                    )
                )
                await db.commit()

    async def remove_member(self, principal_id: str, scope_id: str) -> None:
        async with self._db() as db:
            await db.execute(
                delete(ScopeGrantRow).where(
                    ScopeGrantRow.scope_id == scope_id,
                    ScopeGrantRow.principal_id == principal_id,
                )
            )
            await db.execute(
                delete(ScopeMembershipRow).where(
                    ScopeMembershipRow.scope_id == scope_id,
                    ScopeMembershipRow.principal_id == principal_id,
                )
            )
            await db.commit()

    async def grant(
        self, principal_id: str, scope_id: str, grant: Grant
    ) -> None:
        await self.add_member(principal_id, scope_id)
        async with self._db() as db:
            existing = await db.scalar(
                select(ScopeGrantRow.id).where(
                    ScopeGrantRow.scope_id == scope_id,
                    ScopeGrantRow.principal_id == principal_id,
                    ScopeGrantRow.grant == str(grant),
                )
            )
            if existing is None:
                db.add(
                    ScopeGrantRow(
                        id=uuid4().hex,
                        scope_id=scope_id,
                        principal_id=principal_id,
                        grant=str(grant),
                    )
                )
                await db.commit()

    async def revoke(
        self, principal_id: str, scope_id: str, grant: Grant
    ) -> None:
        async with self._db() as db:
            await db.execute(
                delete(ScopeGrantRow).where(
                    ScopeGrantRow.scope_id == scope_id,
                    ScopeGrantRow.principal_id == principal_id,
                    ScopeGrantRow.grant == str(grant),
                )
            )
            await db.commit()

    async def bootstrap_owner(
        self, principal_id: str, scope_id: str
    ) -> None:
        await self.add_member(principal_id, scope_id)
        for g in ALL_GRANTS:
            await self.grant(principal_id, scope_id, g)


