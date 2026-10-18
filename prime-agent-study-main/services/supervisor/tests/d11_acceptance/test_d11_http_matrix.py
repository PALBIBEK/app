"""D11 T7–T8 — HTTP grant matrix (workspace + presign)."""

from __future__ import annotations

import pytest

from supervisor.seams.authz import Grant
from tests.d11_acceptance.conftest import MEMBER, bootstrap_scope, headers_for


@pytest.mark.asyncio
async def test_t7_http_without_grant_returns_404(postgres_grant_stack) -> None:
    store = postgres_grant_stack["store"]
    grants = postgres_grant_stack["grants"]
    scope = await bootstrap_scope(postgres_grant_stack)
    await store.commit_blob(scope, "secret.txt", b"hidden")

    await grants.add_member(MEMBER, scope)
    await grants.grant(MEMBER, scope, Grant.WORKSPACE_READ)

    client = postgres_grant_stack["client"]
    # read OK
    ok = await client.get(
        f"/v1/scopes/{scope}/files",
        headers=headers_for(MEMBER),
    )
    assert ok.status_code == 200

    # write denied (no workspace:write)
    denied = await client.put(
        f"/v1/scopes/{scope}/files/new.txt",
        content=b"nope",
        headers=headers_for(MEMBER),
    )
    assert denied.status_code == 404


@pytest.mark.asyncio
async def test_t8_presign_without_artifact_download_404(postgres_grant_stack) -> None:
    store = postgres_grant_stack["store"]
    grants = postgres_grant_stack["grants"]
    scope = await bootstrap_scope(postgres_grant_stack)
    await store.commit_blob(scope, "dl.txt", b"data")

    await grants.add_member(MEMBER, scope)
    await grants.grant(MEMBER, scope, Grant.WORKSPACE_READ)

    client = postgres_grant_stack["client"]
    no_dl = await client.post(
        f"/v1/scopes/{scope}/files/dl.txt/download",
        headers=headers_for(MEMBER),
    )
    assert no_dl.status_code == 404

    owner = await client.post(
        f"/v1/scopes/{scope}/files/dl.txt/download",
        headers=postgres_grant_stack["headers"],
    )
    assert owner.status_code == 200, owner.text
    assert owner.json()["url"]
