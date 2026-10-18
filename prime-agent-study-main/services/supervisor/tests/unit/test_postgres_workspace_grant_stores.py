"""Stage 1: Postgres WorkspaceStore + GrantStore parity / durability."""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest

from supervisor.db.engine import open_db
from supervisor.sandbox.local_process import LocalProcessProvider
from supervisor.seams.authz import Grant, NotScopeMember, Principal
from supervisor.seams.authorized_workspace import authorized_hydrate
from supervisor.seams.providers.grant_store_factory import create_grant_store
from supervisor.seams.providers.memory_object_storage import MemoryObjectStorage
from supervisor.seams.providers.memory_workspace_store import MemoryTreeIO
from supervisor.seams.providers.workspace_store_factory import create_workspace_store
from supervisor.seams.workspace_store import CasConflict


@pytest.mark.asyncio
async def test_postgres_grant_store_bootstrap_and_idor() -> None:
    async with open_db("sqlite+aiosqlite:///:memory:") as (_, factory):
        store = create_grant_store("postgres", session_factory=factory)
        await store.bootstrap_owner("owner", "scope-a")
        assert await store.is_member("owner", "scope-a")
        assert await store.has_grant("owner", "scope-a", Grant.SANDBOX_EXEC)
        assert not await store.is_member("evil", "scope-a")
        await store.add_member("reader", "scope-a")
        await store.grant("reader", "scope-a", Grant.WORKSPACE_READ)
        assert await store.has_grant("reader", "scope-a", Grant.WORKSPACE_READ)
        assert not await store.has_grant("reader", "scope-a", Grant.WORKSPACE_WRITE)
        await store.revoke("reader", "scope-a", Grant.WORKSPACE_READ)
        assert not await store.has_grant("reader", "scope-a", Grant.WORKSPACE_READ)


@pytest.mark.asyncio
async def test_postgres_workspace_commit_hydrate_sync_soft_delete() -> None:
    objects = MemoryObjectStorage()
    async with open_db("sqlite+aiosqlite:///:memory:") as (_, factory):
        store = create_workspace_store(
            "postgres", objects=objects, session_factory=factory
        )
        scope = uuid4().hex
        rec = await store.commit_blob(scope, "a.txt", b"hello")
        assert rec.version == 1
        assert await objects.get(rec.object_key) == b"hello"

        tree = MemoryTreeIO()
        report = await store.hydrate(scope, tree)
        assert "a.txt" in report.written
        assert await tree.read_bytes("a.txt") == b"hello"

        await tree.write_bytes("a.txt", b"hello2")
        await tree.write_bytes("b.txt", b"new")
        sync = await store.sync(scope, tree)
        assert "a.txt" in sync.written
        assert "b.txt" in sync.written

        await tree.delete_path("b.txt")
        sync2 = await store.sync(scope, tree)
        assert "b.txt" in sync2.removed
        assert await store.stat(scope, "b.txt") is None

        # Soft-deleted path can be recommitted (CAS from 0)
        await store.commit_blob(scope, "b.txt", b"again", expected_version=0)
        assert (await store.stat(scope, "b.txt")).version == 1


@pytest.mark.asyncio
async def test_postgres_cas_conflict() -> None:
    objects = MemoryObjectStorage()
    async with open_db("sqlite+aiosqlite:///:memory:") as (_, factory):
        store = create_workspace_store(
            "postgres", objects=objects, session_factory=factory
        )
        scope = uuid4().hex
        await store.commit_blob(scope, "x.txt", b"v1", expected_version=0)
        with pytest.raises(CasConflict):
            await store.commit_blob(scope, "x.txt", b"stale", expected_version=0)


@pytest.mark.asyncio
async def test_postgres_survives_new_session_factory_hydrate(tmp_path: Path) -> None:
    """Metadata in SQLite file + object bytes → new factory hydrates same sha."""
    db_path = tmp_path / "ws.db"
    url = f"sqlite+aiosqlite:///{db_path}"
    objects = MemoryObjectStorage()
    scope = uuid4().hex

    async with open_db(url) as (_, factory):
        store = create_workspace_store(
            "postgres", objects=objects, session_factory=factory
        )
        await store.commit_blob(scope, "survive.txt", b"durable-bytes")

    # New process / factory, same DB file + same object store contents
    async with open_db(url) as (_, factory2):
        store2 = create_workspace_store(
            "postgres", objects=objects, session_factory=factory2
        )
        provider = LocalProcessProvider(base_dir=tmp_path / "sbx")
        sb = await provider.create()
        report = await store2.hydrate(scope, sb.as_tree_io())
        assert "survive.txt" in report.written
        assert await sb.read_file("survive.txt") == b"durable-bytes"


@pytest.mark.asyncio
async def test_factory_requires_session_for_postgres() -> None:
    with pytest.raises(ValueError, match="session_factory"):
        create_workspace_store("postgres")
    with pytest.raises(ValueError, match="session_factory"):
        create_grant_store("postgres")


@pytest.mark.asyncio
async def test_postgres_grant_gates_authorized_hydrate(tmp_path: Path) -> None:
    objects = MemoryObjectStorage()
    async with open_db("sqlite+aiosqlite:///:memory:") as (_, factory):
        grants = create_grant_store("postgres", session_factory=factory)
        from supervisor.seams.providers.grant_store_factory import create_authz_service
        from supervisor.seams.workspace_hydrator import StoreWorkspaceHydrator

        authz = create_authz_service(store=grants)
        ws = create_workspace_store(
            "postgres", objects=objects, session_factory=factory
        )
        hydrator = StoreWorkspaceHydrator(ws)
        scope = uuid4().hex
        await grants.bootstrap_owner("owner", scope)
        await ws.commit_blob(scope, "a.txt", b"hi")

        provider = LocalProcessProvider(base_dir=tmp_path / "sbx")
        sb = await provider.create()
        await authorized_hydrate(
            authz, hydrator, Principal(id="owner", tenant_id="t1"), scope, sb.as_tree_io()
        )
        with pytest.raises(NotScopeMember):
            await authorized_hydrate(
                authz,
                hydrator,
                Principal(id="stranger", tenant_id="t1"),
                scope,
                sb.as_tree_io(),
            )
