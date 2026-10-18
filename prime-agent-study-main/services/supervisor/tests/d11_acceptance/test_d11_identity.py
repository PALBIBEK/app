"""D11 T11, T14 — service JWT principal trust (D1)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import jwt
import pytest

from tests.d11_acceptance.conftest import OWNER, bootstrap_scope


@pytest.fixture
async def jwt_grant_stack(tmp_path, fake_s3, s3_objects):
    """Postgres stack with auth_required + service JWT."""
    from supervisor.db.engine import open_db
    from tests.d11_acceptance.conftest import _build_stack

    db_path = tmp_path / "d11_jwt.db"
    url = f"sqlite+aiosqlite:///{db_path}"
    secret = "d11-service-jwt-secret-at-least-32b!!"
    async with open_db(url) as (_, factory):
        stack = await _build_stack(
            tmp_path,
            factory,
            s3_objects=s3_objects,
            auth_required=True,
            service_jwt_secret=secret,
        )
        stack["db_url"] = url
        stack["jwt_secret"] = secret
        try:
            yield stack
        finally:
            await stack["client"].aclose()


def _mint_token(secret: str, *, principal_id: str, tenant_id: str = "d11-tenant") -> str:
    now = datetime.now(UTC)
    return jwt.encode(
        {
            "sub": "prime-agent-bff",
            "iss": "prime-agent-api",
            "aud": "yal-supervisor",
            "type": "service",
            "tenant_id": tenant_id,
            "principal_id": principal_id,
            "iat": now,
            "exp": now + timedelta(seconds=120),
            "jti": uuid4().hex,
        },
        secret,
        algorithm="HS256",
    )


@pytest.mark.asyncio
async def test_t11_jwt_principal_mismatch_header_403(jwt_grant_stack) -> None:
    scope = await bootstrap_scope(jwt_grant_stack)
    client = jwt_grant_stack["client"]
    secret = jwt_grant_stack["jwt_secret"]
    token = _mint_token(secret, principal_id=OWNER)

    ok = await client.get(
        f"/v1/scopes/{scope}/files",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert ok.status_code == 200

    spoof = await client.get(
        f"/v1/scopes/{scope}/files",
        headers={
            "Authorization": f"Bearer {token}",
            "X-Principal-Id": "evil-spoofer",
        },
    )
    assert spoof.status_code == 403


@pytest.mark.asyncio
async def test_t14_auth_required_rejects_bare_headers(jwt_grant_stack) -> None:
    scope = await bootstrap_scope(jwt_grant_stack)
    client = jwt_grant_stack["client"]

    bare = await client.get(
        f"/v1/scopes/{scope}/files",
        headers={"X-Tenant-Id": "d11-tenant", "X-Principal-Id": OWNER},
    )
    assert bare.status_code == 401
