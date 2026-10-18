"""WP-M7 VibeSec — scope IDOR, ready gate, authz fail-closed."""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest

from supervisor.app import build_supervisor
from supervisor.db.engine import open_db
from supervisor.sandbox.local_process import LocalProcessProvider
from supervisor.seams.authz import Grant, GrantDenied, NotScopeMember, Principal
from supervisor.seams.providers.grant_store_factory import (
    create_authz_service,
    create_grant_store,
)
from supervisor.seams.providers.memory_object_storage import MemoryObjectStorage
from supervisor.seams.providers.memory_workspace_store import MemoryWorkspaceStore
from supervisor.seams.session_workspace import (
    SessionWorkspaceService,
    SessionWorkspaceState,
    WorkspaceNotReadyError,
)
from supervisor.seams.workspace_hydrator import StoreWorkspaceHydrator
from tests.support.session_env import make_catalog


@pytest.mark.asyncio
async def test_p0_stranger_cannot_hijack_existing_scope(tmp_path: Path) -> None:
    objects = MemoryObjectStorage()
    store = MemoryWorkspaceStore(objects=objects)
    hydrator = StoreWorkspaceHydrator(store)
    grant_store = create_grant_store("memory")
    authz = create_authz_service(store=grant_store)
    provider = LocalProcessProvider(
        base_dir=tmp_path / "sandboxes", hydrator=hydrator
    )
    scope_id = uuid4().hex

    async with open_db(f"sqlite+aiosqlite:///{tmp_path / 'm7.db'}") as (_, factory):
        state = build_supervisor(
            session_factory=factory,
            catalog=make_catalog(),
            blob_dir=tmp_path / "blobs",
            sandbox_provider=provider,
            authz=authz,
            grant_store=grant_store,
        )
        owner_sid = await state.manager.create(
            tenant_id="t1",
            harness_version="githi-v1",
            runtime_name="githi",
            scope_id=scope_id,
            principal_id="owner",
        )
        assert owner_sid
        assert await grant_store.is_member("owner", scope_id)
        assert await grant_store.has_grant(
            "owner", scope_id, Grant.SANDBOX_EXEC
        )

        with pytest.raises((NotScopeMember, GrantDenied)):
            await state.manager.create(
                tenant_id="evil",
                harness_version="githi-v1",
                runtime_name="githi",
                scope_id=scope_id,
                principal_id="evil",
            )

        assert not await grant_store.is_member("evil", scope_id)
        assert await grant_store.has_grant(
            "owner", scope_id, Grant.WORKSPACE_WRITE
        )


@pytest.mark.asyncio
async def test_p0_scope_without_authz_fails_closed() -> None:
    svc = SessionWorkspaceService(authz=None, hydrator=None)
    state = SessionWorkspaceState(
        scope_id="s1",
        principal=Principal(id="p", tenant_id="t"),
    )
    with pytest.raises(RuntimeError, match="requires authz"):
        await svc.require_sandbox_exec(state)


@pytest.mark.asyncio
async def test_s8_invoke_tool_refuses_when_not_ready(tmp_path: Path) -> None:
    objects = MemoryObjectStorage()
    store = MemoryWorkspaceStore(objects=objects)
    hydrator = StoreWorkspaceHydrator(store)
    grant_store = create_grant_store("memory")
    authz = create_authz_service(store=grant_store)
    provider = LocalProcessProvider(
        base_dir=tmp_path / "sandboxes", hydrator=hydrator
    )
    scope_id = uuid4().hex

    async with open_db(f"sqlite+aiosqlite:///{tmp_path / 'm7b.db'}") as (_, factory):
        state = build_supervisor(
            session_factory=factory,
            catalog=make_catalog(),
            blob_dir=tmp_path / "blobs",
            sandbox_provider=provider,
            authz=authz,
            grant_store=grant_store,
        )
        sid = await state.manager.create(
            tenant_id="t1",
            harness_version="githi-v1",
            runtime_name="githi",
            scope_id=scope_id,
            principal_id="t1",
        )
        ws = state.manager._workspace[sid]
        ws.ready = False
        with pytest.raises(WorkspaceNotReadyError):
            await state.manager.invoke_tool(
                sid,
                tenant_id="t1",
                name="write",
                arguments={"file_path": "x.txt", "content": "nope"},
            )


@pytest.mark.asyncio
async def test_s2_read_only_cannot_sync(tmp_path: Path) -> None:
    objects = MemoryObjectStorage()
    store = MemoryWorkspaceStore(objects=objects)
    hydrator = StoreWorkspaceHydrator(store)
    grant_store = create_grant_store("memory")
    authz = create_authz_service(store=grant_store)
    scope = uuid4().hex
    await grant_store.bootstrap_owner("owner", scope)
    await grant_store.add_member("reader", scope)
    await grant_store.grant("reader", scope, Grant.WORKSPACE_READ)

    svc = SessionWorkspaceService(authz=authz, hydrator=hydrator)
    provider = LocalProcessProvider(
        base_dir=tmp_path / "sbx", hydrator=hydrator
    )
    sb = await provider.create()
    state = SessionWorkspaceState(
        scope_id=scope,
        principal=Principal(id="reader", tenant_id="t1"),
        dirty=True,
        ready=True,
    )
    with pytest.raises(GrantDenied):
        await svc.sync_if_dirty(state, sb, force=True)
