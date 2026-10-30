"""Erasing a principal removes their sessions and workspaces, and hands shared work on.

There was no way to: a person who deleted their account left every conversation,
workspace file and secret of theirs behind in the supervisor.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from supervisor.agent.events import MessageEnd
from supervisor.db.engine import open_db
from supervisor.db.models import (
    ErasedSessionRow,
    EventRow,
    ScopeGrantRow,
    ScopeMembershipRow,
    ScopeRow,
    SessionRow,
    TenantSpendRow,
    VaultSecretRow,
    WorkspaceFileRow,
    WorkspaceFileVersionRow,
)
from supervisor.journal.writer import JournalWriter
from supervisor.seams.providers.authz.postgres_grant_store import PostgresGrantStore
from supervisor.seams.providers.storage.memory_object_storage import MemoryObjectStorage
from supervisor.seams.providers.workspace.postgres_workspace_store import PostgresWorkspaceStore
from supervisor.seams.services.authz import ALL_GRANTS, Grant
from supervisor.session.erase import SessionEraser
from supervisor.session.erase_principal import PrincipalErased, PrincipalEraser

pytestmark = pytest.mark.asyncio

ALICE, BOB, CAROL, DAVE = "alice", "bob", "carol", "dave"


@pytest.fixture
async def platform(tmp_path: Path) -> AsyncIterator[dict[str, Any]]:
    async with open_db(f"sqlite+aiosqlite:///{tmp_path / 'erase.db'}") as (_, factory):
        objects = MemoryObjectStorage()
        forgotten: list[tuple[str, str]] = []
        yield {
            "factory": factory,
            "objects": objects,
            "writer": JournalWriter(factory, blob_dir=tmp_path / "blobs"),
            "grants": PostgresGrantStore(factory),
            "store": PostgresWorkspaceStore(factory, objects),
            "forgotten": forgotten,
            "eraser": PrincipalEraser(
                factory,
                sessions=SessionEraser(factory, blob_dir=tmp_path / "blobs", objects=objects),
                objects=objects,
                forget_secret=lambda tenant, name: forgotten.append((tenant, name)),
            ),
        }


async def _session(p: dict[str, Any], sid: str, *, tenant: str, owner: str | None) -> None:
    await p["writer"].ensure_session(sid, tenant_id=tenant)
    await p["writer"].append(sid, MessageEnd(role="user", content=[{"type": "text", "text": "hi"}]), turn_id="r1", trace_id=None)
    factory: async_sessionmaker[AsyncSession] = p["factory"]
    async with factory() as db:
        row = await db.get(SessionRow, sid)
        assert row is not None
        row.owner_principal_id = owner
        await db.commit()


async def _scope(p: dict[str, Any], scope_id: str, owner: str, *members: str) -> None:
    async with p["factory"]() as db:
        db.add(ScopeRow(id=scope_id, type="personal", owner_principal_id=owner, name=scope_id))
        await db.commit()
    await p["grants"].bootstrap_owner(owner, scope_id)
    for member in members:
        await p["grants"].grant(member, scope_id, Grant.WORKSPACE_READ)
    await p["store"].commit_blob(scope_id, "notes.md", f"notes of {scope_id}".encode())
    await p["store"].commit_blob(scope_id, "notes.md", f"more notes of {scope_id}".encode())


async def _secret(p: dict[str, Any], tenant: str, name: str) -> None:
    async with p["factory"]() as db:
        db.add(VaultSecretRow(id=f"{tenant}-{name}", owner_kind="tenant", owner_id=tenant, name=name, key_id="k1",
                              nonce=b"n", ciphertext=b"c", expires_at=datetime.now(UTC) + timedelta(hours=1)))
        await db.commit()


async def _count(p: dict[str, Any], row: Any, *where: Any) -> int:
    async with p["factory"]() as db:
        return int(await db.scalar(select(func.count()).select_from(row).where(*where)) or 0)


async def _grants(p: dict[str, Any], scope_id: str, principal: str) -> set[str]:
    async with p["factory"]() as db:
        found = await db.scalars(
            select(ScopeGrantRow.grant).where(ScopeGrantRow.scope_id == scope_id, ScopeGrantRow.principal_id == principal)
        )
        return set(found)


@pytest.fixture
async def world(platform: dict[str, Any]) -> dict[str, Any]:
    """Alice is her own tenant (as the product's people are), with company."""
    p = platform
    await _session(p, "a-mine", tenant=ALICE, owner=ALICE)
    await _session(p, "a-unrecorded", tenant=ALICE, owner=None)  # the tenant's: hers
    await _session(p, "a-daves", tenant=ALICE, owner=DAVE)  # someone else's in her tenant
    await _session(p, "b-mine", tenant=BOB, owner=BOB)
    await _scope(p, "alone", ALICE)  # only hers
    await _scope(p, "shared", ALICE, BOB, CAROL)  # hers, with members
    await _scope(p, "carols", CAROL, ALICE)  # someone else's, she belongs to
    await _secret(p, ALICE, "mcp:https://tools.example.com")
    await _secret(p, BOB, "mcp:https://tools.example.com")
    async with p["factory"]() as db:
        db.add_all([TenantSpendRow(tenant_id=t, month="2026-10", cost_usd=1) for t in (ALICE, BOB)])
        await db.commit()
    return p


async def test_erasing_takes_her_sessions_and_her_workspace(world: dict[str, Any]) -> None:
    p = world
    alone_objects = await _keys(p, "alone")
    assert len(alone_objects) == 2  # two versions, each its own bytes

    erased = await p["eraser"].erase(ALICE, ALICE)

    assert erased == PrincipalErased(sessions=2, scopes=1, scopes_handed_on=1, workspace_files=1, secrets=1)
    assert await _count(p, SessionRow, SessionRow.id.in_(["a-mine", "a-unrecorded"])) == 0
    assert await _count(p, EventRow, EventRow.session_id.in_(["a-mine", "a-unrecorded"])) == 0
    for row in (WorkspaceFileRow, WorkspaceFileVersionRow, ScopeGrantRow, ScopeMembershipRow):
        assert await _count(p, row, row.scope_id == "alone") == 0, row.__tablename__
    assert await _count(p, ScopeRow, ScopeRow.id == "alone") == 0
    for key in alone_objects:
        assert not await p["objects"].exists(key)
    assert await _count(p, VaultSecretRow, VaultSecretRow.owner_id == ALICE) == 0
    assert await _count(p, TenantSpendRow, TenantSpendRow.tenant_id == ALICE) == 0
    assert await _count(p, ErasedSessionRow, ErasedSessionRow.tenant_id == ALICE) == 0  # no trace left
    assert p["forgotten"] == [(ALICE, "mcp:https://tools.example.com")]


async def test_shared_work_goes_to_the_member_who_joined_first(world: dict[str, Any]) -> None:
    p = world
    await p["eraser"].erase(ALICE, ALICE)

    async with p["factory"]() as db:
        shared = await db.get(ScopeRow, "shared")
    assert shared is not None and shared.owner_principal_id == BOB
    assert await _grants(p, "shared", BOB) == {str(g) for g in ALL_GRANTS}
    assert await _grants(p, "shared", CAROL) == {str(Grant.WORKSPACE_READ)}
    assert await _count(p, WorkspaceFileVersionRow, WorkspaceFileVersionRow.scope_id == "shared") == 2
    # She is no longer anywhere: not in the shared scope, not in Carol's.
    assert await _count(p, ScopeMembershipRow, ScopeMembershipRow.principal_id == ALICE) == 0
    assert await _count(p, ScopeGrantRow, ScopeGrantRow.principal_id == ALICE) == 0
    assert await p["grants"].is_member(CAROL, "carols")


async def test_no_one_elses_data_goes(world: dict[str, Any]) -> None:
    p = world
    await p["eraser"].erase(ALICE, ALICE)
    assert await _count(p, SessionRow, SessionRow.id.in_(["a-daves", "b-mine"])) == 2
    assert await _count(p, EventRow, EventRow.session_id == "b-mine") == 1
    assert await _count(p, VaultSecretRow, VaultSecretRow.owner_id == BOB) == 1
    assert await _count(p, TenantSpendRow, TenantSpendRow.tenant_id == BOB) == 1
    assert await _count(p, WorkspaceFileRow, WorkspaceFileRow.scope_id == "carols") == 1


async def test_a_member_of_a_shared_tenant_keeps_its_secrets(world: dict[str, Any]) -> None:
    p = world
    erased = await p["eraser"].erase(ALICE, DAVE)  # Dave, in Alice's tenant
    assert erased.sessions == 1 and erased.secrets == 0
    assert await _count(p, SessionRow, SessionRow.id == "a-daves") == 0
    assert await _count(p, SessionRow, SessionRow.id.in_(["a-mine", "a-unrecorded"])) == 2
    assert await _count(p, VaultSecretRow, VaultSecretRow.owner_id == ALICE) == 1


async def test_erasing_again_finds_nothing_left(world: dict[str, Any]) -> None:
    await world["eraser"].erase(ALICE, ALICE)
    assert await world["eraser"].erase(ALICE, ALICE) == PrincipalErased()


async def _keys(p: dict[str, Any], scope_id: str) -> list[str]:
    async with p["factory"]() as db:
        found = await db.scalars(
            select(WorkspaceFileVersionRow.object_key).where(WorkspaceFileVersionRow.scope_id == scope_id)
        )
        return list(found)
