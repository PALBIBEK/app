"""D11 T1, T3–T6 — durable postgres grants + restart survival."""

from __future__ import annotations

from uuid import uuid4

import pytest

from supervisor.seams.authz import ALL_GRANTS, Grant, GrantDenied, Principal
from supervisor.seams.authorized_workspace import authorized_hydrate, authorized_sync
from supervisor.seams.workspace_hydrator import StoreWorkspaceHydrator
from tests.d11_acceptance.conftest import (
    MEMBER,
    OWNER,
    STRANGER,
    bootstrap_scope,
    headers_for,
    reopen_stack,
)


@pytest.mark.asyncio
async def test_t1_bootstrap_owner_has_all_mvp_grants(postgres_grant_stack) -> None:
    grants = postgres_grant_stack["grants"]
    scope = await bootstrap_scope(postgres_grant_stack)
    listed = await grants.list_grants(OWNER, scope)
    assert frozenset(listed) == frozenset(ALL_GRANTS)
    for g in ALL_GRANTS:
        assert await grants.has_grant(OWNER, scope, g)


@pytest.mark.asyncio
async def test_t3_restart_owner_hydrate_still_allowed(postgres_grant_stack) -> None:
    store = postgres_grant_stack["store"]
    scope = await bootstrap_scope(postgres_grant_stack)
    await store.commit_blob(scope, "persist.txt", b"durable")

    restarted = await reopen_stack(postgres_grant_stack)
    client = restarted["client"]
    headers = restarted["headers"]

    listing = await client.get(f"/v1/scopes/{scope}/files", headers=headers)
    assert listing.status_code == 200, listing.text
    paths = [r["path"] for r in listing.json()]
    assert "persist.txt" in paths

    provider = restarted["provider"]
    sb = await provider.create()
    hydrator = StoreWorkspaceHydrator(restarted["store"])
    await authorized_hydrate(
        restarted["authz"],
        hydrator,
        Principal(id=OWNER, tenant_id="d11-tenant"),
        scope,
        sb.as_tree_io(),
    )
    assert await sb.read_file("persist.txt") == b"durable"


@pytest.mark.asyncio
async def test_t3b_restart_owner_sandbox_exec_still_allowed(
    postgres_grant_stack,
) -> None:
    """Post-restart owner can still create scoped session (sandbox:exec retained)."""
    scope = await bootstrap_scope(postgres_grant_stack)
    restarted = await reopen_stack(postgres_grant_stack)
    client = restarted["client"]
    headers = restarted["headers"]

    cr = await client.post(
        "/v1/sessions",
        json={
            "harness_version": "githi-v1",
            "runtime": "githi",
            "scope_id": scope,
        },
        headers=headers,
    )
    assert cr.status_code == 201, cr.text
    assert cr.json().get("session_id")


@pytest.mark.asyncio
async def test_t4_restart_stranger_still_denied(postgres_grant_stack) -> None:
    scope = await bootstrap_scope(postgres_grant_stack)
    restarted = await reopen_stack(postgres_grant_stack)
    client = restarted["client"]

    denied = await client.get(
        f"/v1/scopes/{scope}/files",
        headers=headers_for(STRANGER),
    )
    assert denied.status_code == 404


@pytest.mark.asyncio
async def test_t5_revoke_write_sync_denied_read_ok(postgres_grant_stack) -> None:
    store = postgres_grant_stack["store"]
    grants = postgres_grant_stack["grants"]
    authz = postgres_grant_stack["authz"]
    scope = await bootstrap_scope(postgres_grant_stack)
    await store.commit_blob(scope, "rw.txt", b"v1")

    await grants.revoke(OWNER, scope, Grant.WORKSPACE_WRITE)

    client = postgres_grant_stack["client"]
    headers = postgres_grant_stack["headers"]
    got = await client.get(f"/v1/scopes/{scope}/files/rw.txt", headers=headers)
    assert got.status_code == 200

    put = await client.put(
        f"/v1/scopes/{scope}/files/rw.txt",
        content=b"v2",
        headers=headers,
    )
    assert put.status_code == 404

    provider = postgres_grant_stack["provider"]
    sb = await provider.create()
    tree = sb.as_tree_io()
    hydrator = StoreWorkspaceHydrator(store)
    await authorized_hydrate(
        authz, hydrator, Principal(id=OWNER, tenant_id="d11-tenant"), scope, tree
    )
    with pytest.raises(GrantDenied):
        await authorized_sync(
            authz, store, Principal(id=OWNER, tenant_id="d11-tenant"), scope, tree
        )


@pytest.mark.asyncio
async def test_t6_remove_membership_all_grants_fail(postgres_grant_stack) -> None:
    grants = postgres_grant_stack["grants"]
    scope = await bootstrap_scope(postgres_grant_stack)
    await grants.add_member(MEMBER, scope)
    await grants.grant(MEMBER, scope, Grant.WORKSPACE_READ)
    await grants.remove_member(MEMBER, scope)

    client = postgres_grant_stack["client"]
    denied = await client.get(
        f"/v1/scopes/{scope}/files",
        headers=headers_for(MEMBER),
    )
    assert denied.status_code == 404
    assert not await grants.is_member(MEMBER, scope)
