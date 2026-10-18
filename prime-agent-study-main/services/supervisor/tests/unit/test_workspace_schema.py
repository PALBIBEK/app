"""WP-M2: scopes + workspace_files schema roundtrip (sqlite in-memory)."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from supervisor.db.models import Base, ScopeRow, WorkspaceFileRow


@pytest.fixture
async def db_session() -> AsyncSession:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


@pytest.mark.asyncio
async def test_scope_and_workspace_file_roundtrip(db_session: AsyncSession) -> None:
    now = datetime.now(timezone.utc)
    scope_id = uuid.uuid4().hex
    file_id = uuid.uuid4().hex
    db_session.add(
        ScopeRow(
            id=scope_id,
            type="personal",
            owner_principal_id="user-1",
            name="default",
            created_at=now,
        )
    )
    db_session.add(
        WorkspaceFileRow(
            id=file_id,
            scope_id=scope_id,
            path="readme.md",
            content_sha256="a" * 64,
            object_key="blobs/aa/" + "a" * 64,
            size_bytes=12,
            mime_type="text/markdown",
            version=1,
            created_at=now,
            updated_at=now,
        )
    )
    await db_session.commit()

    rows = (
        await db_session.execute(
            select(WorkspaceFileRow).where(
                WorkspaceFileRow.scope_id == scope_id,
                WorkspaceFileRow.deleted_at.is_(None),
            )
        )
    ).scalars().all()
    assert len(rows) == 1
    assert rows[0].path == "readme.md"
    assert rows[0].version == 1


@pytest.mark.asyncio
async def test_soft_delete_marks_deleted_at(db_session: AsyncSession) -> None:
    now = datetime.now(timezone.utc)
    scope_id = uuid.uuid4().hex
    db_session.add(
        ScopeRow(
            id=scope_id,
            type="project",
            owner_principal_id="user-2",
            name="proj",
            created_at=now,
        )
    )
    row = WorkspaceFileRow(
        id=uuid.uuid4().hex,
        scope_id=scope_id,
        path="gone.txt",
        content_sha256="b" * 64,
        object_key="blobs/bb/" + "b" * 64,
        size_bytes=1,
        version=1,
        created_at=now,
        updated_at=now,
    )
    db_session.add(row)
    await db_session.commit()

    row.deleted_at = datetime.now(timezone.utc)
    await db_session.commit()

    live = (
        await db_session.execute(
            select(WorkspaceFileRow).where(
                WorkspaceFileRow.scope_id == scope_id,
                WorkspaceFileRow.deleted_at.is_(None),
            )
        )
    ).scalars().all()
    assert live == []
