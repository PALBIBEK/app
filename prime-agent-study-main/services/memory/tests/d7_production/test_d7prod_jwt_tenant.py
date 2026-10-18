"""D7 FULL-PROD — JWT tenant claim preferred; X-Tenant-Id cannot override."""

from __future__ import annotations

from starlette.testclient import TestClient

from .conftest import STATIC_TOKEN, mint_service_jwt


def test_prod_jwt_tenant_claim_path_writes_item(jwt_only: TestClient) -> None:
    """Verified JWT tenant_id claim is a first-class auth path (not header-only)."""
    token = mint_service_jwt(tenant_id="tenant-jwt-a")
    r = jwt_only.post(
        "/v1/memory/items",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "scope": {"tenant": "tenant-jwt-a", "subject": "s", "namespace": "n"},
            "content": "d7-prod jwt claim fact",
        },
    )
    assert r.status_code == 201, r.text
    assert r.json().get("id")


def test_prod_x_tenant_id_cannot_override_jwt_tenant(jwt_only: TestClient) -> None:
    """X-Tenant-Id must not win over verified JWT tenant_id (anti-IDOR spoof)."""
    token = mint_service_jwt(tenant_id="tenant-jwt-a")
    # Spoof header claiming a different tenant while JWT says tenant-jwt-a.
    r = jwt_only.post(
        "/v1/memory/items",
        headers={
            "Authorization": f"Bearer {token}",
            "X-Tenant-Id": "tenant-spoof-b",
        },
        json={
            "scope": {"tenant": "tenant-spoof-b", "subject": "s", "namespace": "n"},
            "content": "should not land under spoofed tenant",
        },
    )
    assert r.status_code in {401, 403}, r.text
    if r.headers.get("content-type", "").startswith("application/json"):
        body = r.json()
        assert "should not land" not in str(body)


def test_prod_jwt_scope_must_match_claim(jwt_only: TestClient) -> None:
    """Body scope.tenant must match JWT tenant_id even without X-Tenant-Id."""
    token = mint_service_jwt(tenant_id="tenant-jwt-a")
    r = jwt_only.post(
        "/v1/memory/items",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "scope": {"tenant": "tenant-other", "subject": "s", "namespace": "n"},
            "content": "cross-scope",
        },
    )
    assert r.status_code == 403, r.text


def test_prod_static_token_alone_insufficient_as_staging_primary(
    static_only_staging: TestClient,
) -> None:
    """Staging primary path must be JWT claims — static MEMORY_SERVICE_TOKEN alone is residual/CI-only.

    Production reopen: without MEMORY_ALLOW_STATIC_TOKEN=1, staging must not treat
    shared-token + X-Tenant-Id as the primary authenticated path.
    """
    r = static_only_staging.post(
        "/v1/memory/items",
        headers={
            "Authorization": f"Bearer {STATIC_TOKEN}",
            "X-Tenant-Id": "tenant-static",
        },
        json={
            "scope": {"tenant": "tenant-static", "subject": "s", "namespace": "n"},
            "content": "static-primary-must-fail",
        },
    )
    # Accept fail-closed auth (401/403) or AUTH_UNCONFIGURED (503) — not 201.
    assert r.status_code in {401, 403, 503}, (
        f"D7 FULL-PROD: static MEMORY_SERVICE_TOKEN alone must not succeed as "
        f"staging primary auth (got {r.status_code}): {r.text}"
    )
