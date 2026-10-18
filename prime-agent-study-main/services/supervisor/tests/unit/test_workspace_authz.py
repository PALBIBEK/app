"""WP-M4: principal → scope → grants (AuthzService + GrantStore; fakes only)."""

from __future__ import annotations

from pathlib import Path

import pytest

from supervisor.sandbox.local_process import LocalProcessProvider
from supervisor.seams.authz import (
    Grant,
    GrantDenied,
    NotScopeMember,
    Principal,
)
from supervisor.seams.authorized_workspace import authorized_hydrate, authorized_sync
from supervisor.seams.providers.grant_store_factory import (
    create_authz_service,
    create_grant_store,
)
from supervisor.seams.providers.memory_grant_store import MemoryGrantStore
from supervisor.seams.providers.memory_object_storage import MemoryObjectStorage
from supervisor.seams.providers.memory_workspace_store import MemoryWorkspaceStore
from supervisor.seams.workspace_hydrator import as_workspace_hydrator


@pytest.fixture
def store() -> MemoryGrantStore:
    return MemoryGrantStore()


@pytest.fixture
def authz(store: MemoryGrantStore):
    return create_authz_service(store=store)


@pytest.fixture
def owner() -> Principal:
    return Principal(id="owner-1", tenant_id="t1")


@pytest.fixture
def member() -> Principal:
    return Principal(id="member-1", tenant_id="t1")


@pytest.mark.asyncio
async def test_owner_bootstrap_gets_all_grants(
    store: MemoryGrantStore, authz, owner: Principal
) -> None:
    await store.bootstrap_owner(owner.id, "scope-a")
    for g in Grant:
        assert await authz.check(owner, "scope-a", g)
        await authz.require(owner, "scope-a", g)


@pytest.mark.asyncio
async def test_non_member_denied(
    store: MemoryGrantStore, authz, member: Principal
) -> None:
    await store.bootstrap_owner("someone-else", "scope-a")
    with pytest.raises(NotScopeMember):
        await authz.require(member, "scope-a", Grant.WORKSPACE_READ)


@pytest.mark.asyncio
async def test_member_missing_write_denied(
    store: MemoryGrantStore, authz, member: Principal
) -> None:
    await store.add_member(member.id, "scope-a")
    await store.grant(member.id, "scope-a", Grant.WORKSPACE_READ)
    await authz.require(member, "scope-a", Grant.WORKSPACE_READ)
    with pytest.raises(GrantDenied):
        await authz.require(member, "scope-a", Grant.WORKSPACE_WRITE)


@pytest.mark.asyncio
async def test_revoke_then_recheck_fails(
    store: MemoryGrantStore, authz, member: Principal
) -> None:
    await store.grant(member.id, "scope-a", Grant.SANDBOX_EXEC)
    await authz.require(member, "scope-a", Grant.SANDBOX_EXEC)
    await store.revoke(member.id, "scope-a", Grant.SANDBOX_EXEC)
    with pytest.raises(GrantDenied):
        await authz.require(member, "scope-a", Grant.SANDBOX_EXEC)


@pytest.mark.asyncio
async def test_artifact_download_and_admin_gates(
    store: MemoryGrantStore, authz, member: Principal
) -> None:
    await store.add_member(member.id, "scope-a")
    with pytest.raises(GrantDenied):
        await authz.require(member, "scope-a", Grant.ARTIFACT_DOWNLOAD)
    await store.grant(member.id, "scope-a", Grant.ARTIFACT_DOWNLOAD)
    await authz.require(member, "scope-a", Grant.ARTIFACT_DOWNLOAD)
    with pytest.raises(GrantDenied):
        await authz.require(member, "scope-a", Grant.SCOPE_ADMIN)


@pytest.mark.asyncio
async def test_authorized_hydrate_sync_gates(
    store: MemoryGrantStore,
    authz,
    owner: Principal,
    member: Principal,
    tmp_path: Path,
) -> None:
    scope = "scope-authz"
    await store.bootstrap_owner(owner.id, scope)
    await store.add_member(member.id, scope)
    await store.grant(member.id, scope, Grant.WORKSPACE_READ)

    ws = MemoryWorkspaceStore(objects=MemoryObjectStorage())
    await ws.commit_blob(scope, "a.txt", b"hi", expected_version=0)
    hydrator = as_workspace_hydrator(ws)
    provider = LocalProcessProvider(base_dir=tmp_path / "sbx", hydrator=hydrator)
    sb = await provider.create()

    report = await authorized_hydrate(
        authz, hydrator, member, scope, sb.as_tree_io()
    )
    assert "a.txt" in report.written

    (sb.root / "a.txt").write_bytes(b"dirty")
    with pytest.raises(GrantDenied):
        await authorized_sync(authz, hydrator, member, scope, sb.as_tree_io())

    sync = await authorized_sync(authz, hydrator, owner, scope, sb.as_tree_io())
    assert "a.txt" in sync.written


@pytest.mark.asyncio
async def test_factory_memory_default() -> None:
    assert isinstance(create_grant_store("memory"), MemoryGrantStore)
    svc = create_authz_service("memory")
    principal = Principal(id="p")
    with pytest.raises(NotScopeMember):
        await svc.require(principal, "missing", Grant.WORKSPACE_READ)


@pytest.mark.asyncio
async def test_grant_vocabulary_matches_contract() -> None:
    assert Grant.WORKSPACE_READ == "workspace:read"
    assert Grant.WORKSPACE_WRITE == "workspace:write"
    assert Grant.SANDBOX_EXEC == "sandbox:exec"
    assert Grant.ARTIFACT_DOWNLOAD == "artifact:download"
    assert Grant.SCOPE_ADMIN == "scope:admin"
    assert set(Grant) == {
        Grant.WORKSPACE_READ,
        Grant.WORKSPACE_WRITE,
        Grant.SANDBOX_EXEC,
        Grant.ARTIFACT_DOWNLOAD,
        Grant.SCOPE_ADMIN,
    }
