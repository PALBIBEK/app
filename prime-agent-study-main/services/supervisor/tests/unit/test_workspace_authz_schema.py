"""WP-M4 schema: scope memberships/grants + sessions.scope_id."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from supervisor.db.models import (
    Base,
    ScopeGrantRow,
    ScopeMembershipRow,
    ScopeRow,
    SessionRow,
)


@pytest.fixture
async def db() -> async_sessionmaker[AsyncSession]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory
    await engine.dispose()


@pytest.mark.asyncio
async def test_membership_and_grant_rows(db: async_sessionmaker[AsyncSession]) -> None:
    now = datetime.now(timezone.utc)
    scope_id = uuid.uuid4().hex
    async with db() as session:
        session.add(
            ScopeRow(
                id=scope_id,
                type="personal",
                owner_principal_id="owner-1",
                name="demo",
                created_at=now,
            )
        )
        session.add(
            ScopeMembershipRow(
                id=uuid.uuid4().hex,
                scope_id=scope_id,
                principal_id="member-1",
                created_at=now,
            )
        )
        session.add(
            ScopeGrantRow(
                id=uuid.uuid4().hex,
                scope_id=scope_id,
                principal_id="member-1",
                grant="workspace:read",
                created_at=now,
            )
        )
        session.add(
            SessionRow(
                id=uuid.uuid4().hex,
                tenant_id="t1",
                scope_id=scope_id,
                runtime="echo",
                created_at=now,
                updated_at=now,
            )
        )
        await session.commit()

    async with db() as session:
        members = (
            await session.execute(
                select(ScopeMembershipRow).where(ScopeMembershipRow.scope_id == scope_id)
            )
        ).scalars().all()
        assert len(members) == 1
        assert members[0].principal_id == "member-1"
        grants = (
            await session.execute(
                select(ScopeGrantRow).where(ScopeGrantRow.scope_id == scope_id)
            )
        ).scalars().all()
        assert grants[0].grant == "workspace:read"
        sessions = (
            await session.execute(select(SessionRow).where(SessionRow.scope_id == scope_id))
        ).scalars().all()
        assert len(sessions) == 1
