"""D11 T9–T10 — scope:admin invite / member exec."""

from __future__ import annotations

import pytest

from tests.d11_acceptance.conftest import MEMBER, OWNER, bootstrap_scope, headers_for


@pytest.mark.asyncio
async def test_t9_non_admin_invite_403(postgres_grant_stack) -> None:
    scope = await bootstrap_scope(postgres_grant_stack)
    client = postgres_grant_stack["client"]

    denied = await client.post(
        f"/v1/scopes/{scope}/members",
        json={"principal_id": "invitee-1", "grants": ["workspace:read"]},
        headers=headers_for(MEMBER),
    )
    assert denied.status_code == 403


@pytest.mark.asyncio
async def test_t10_admin_invite_then_member_exec(postgres_grant_stack) -> None:
    scope = await bootstrap_scope(postgres_grant_stack)
    client = postgres_grant_stack["client"]
    headers = postgres_grant_stack["headers"]

    invite = await client.post(
        f"/v1/scopes/{scope}/members",
        json={
            "principal_id": MEMBER,
            "grants": ["workspace:read", "sandbox:exec"],
        },
        headers=headers,
    )
    assert invite.status_code == 201, invite.text
    body = invite.json()
    assert body["principal_id"] == MEMBER
    assert "sandbox:exec" in body["grants"]

    listed = await client.get(f"/v1/scopes/{scope}/members", headers=headers)
    assert listed.status_code == 200
    principals = {m["principal_id"] for m in listed.json()}
    assert OWNER in principals
    assert MEMBER in principals

    session = await client.post(
        "/v1/sessions",
        json={
            "harness_version": "githi-v1",
            "runtime": "githi",
            "scope_id": scope,
        },
        headers=headers_for(MEMBER),
    )
    assert session.status_code == 201, session.text

    revoke = await client.delete(
        f"/v1/scopes/{scope}/members/{MEMBER}",
        headers=headers,
    )
    assert revoke.status_code == 204

    blocked = await client.post(
        "/v1/sessions",
        json={
            "harness_version": "githi-v1",
            "runtime": "githi",
            "scope_id": scope,
        },
        headers=headers_for(MEMBER),
    )
    assert blocked.status_code == 403
