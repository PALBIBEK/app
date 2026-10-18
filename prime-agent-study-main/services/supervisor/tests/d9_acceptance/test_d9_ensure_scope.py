"""D9 — POST /v1/scopes/{scope_id}/ensure (BFF upload-before-session bootstrap)."""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest

from supervisor.db.models import ScopeRow
from supervisor.seams.authz import Grant
from tests.d9_acceptance.conftest import PRINCIPAL, TENANT

ROOT = Path(__file__).resolve().parents[4]
COMPOSE = ROOT / "docker-compose.yml"


def test_compose_loads_composition_catalog_by_default() -> None:
    """LOCAL_DEMO: catalog must load so bind does not 503 CATALOG_UNAVAILABLE."""
    text = COMPOSE.read_text(encoding="utf-8")
    assert "YAL_LOAD_COMPOSITION_CATALOG: ${YAL_LOAD_COMPOSITION_CATALOG:-true}" in text
    assert "YAL_CATALOG_DIR: ${YAL_CATALOG_DIR:-/harness}" in text
    assert "HARNESS_VERSIONS_DIR: ${HARNESS_VERSIONS_DIR:-/harness/versions}" in text
    assert "./harness:/harness:ro" in text
    assert "./contracts:/contracts:ro" in text
    # Escape hatch remains documented / overridable.
    assert "YAL_LOAD_COMPOSITION_CATALOG=false" in text


@pytest.mark.asyncio
async def test_ensure_creates_scope_and_owner_grants(postgres_s3_stack) -> None:
    client = postgres_s3_stack["client"]
    grants = postgres_s3_stack["grants"]
    factory = postgres_s3_stack["factory"]
    scope = f"ensure-{uuid4().hex[:12]}"

    first = await client.post(
        f"/v1/scopes/{scope}/ensure",
        headers=postgres_s3_stack["headers"],
    )
    assert first.status_code == 200, first.text
    body = first.json()
    assert body["scope_id"] == scope
    assert body["status"] == "ok"

    async with factory() as db:
        row = await db.get(ScopeRow, scope)
        assert row is not None
        assert row.owner_principal_id == PRINCIPAL

    assert await grants.is_member(PRINCIPAL, scope)
    assert await grants.has_grant(PRINCIPAL, scope, Grant.WORKSPACE_WRITE)
    assert await grants.has_grant(PRINCIPAL, scope, Grant.SCOPE_ADMIN)

    again = await client.post(
        f"/v1/scopes/{scope}/ensure",
        headers=postgres_s3_stack["headers"],
    )
    assert again.status_code == 200, again.text
    assert again.json() == {"scope_id": scope, "status": "ok"}


@pytest.mark.asyncio
async def test_ensure_existing_scope_rejects_stranger(postgres_s3_stack) -> None:
    client = postgres_s3_stack["client"]
    grants = postgres_s3_stack["grants"]
    headers = postgres_s3_stack["headers"]
    scope = f"ensure-idor-{uuid4().hex[:10]}"

    owned = await client.post(f"/v1/scopes/{scope}/ensure", headers=headers)
    assert owned.status_code == 200, owned.text

    foreign = await client.post(
        f"/v1/scopes/{scope}/ensure",
        headers={"X-Tenant-Id": TENANT, "X-Principal-Id": "stranger-ensure"},
    )
    assert foreign.status_code == 403
    assert not await grants.is_member("stranger-ensure", scope)


@pytest.mark.asyncio
async def test_ensure_then_put_file(postgres_s3_stack) -> None:
    client = postgres_s3_stack["client"]
    headers = postgres_s3_stack["headers"]
    scope = f"ensure-put-{uuid4().hex[:10]}"

    ensure = await client.post(f"/v1/scopes/{scope}/ensure", headers=headers)
    assert ensure.status_code == 200, ensure.text

    put = await client.put(
        f"/v1/scopes/{scope}/files/upload.txt",
        content=b"hello-ensure",
        headers={**headers, "Content-Type": "text/plain"},
        params={"mime_type": "text/plain"},
    )
    assert put.status_code == 200, put.text
    assert put.json()["path"] == "upload.txt"

    listed = await client.get(f"/v1/scopes/{scope}/files", headers=headers)
    assert listed.status_code == 200
    assert any(r["path"] == "upload.txt" for r in listed.json())
