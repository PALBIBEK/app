"""A user's session is theirs: other users in the tenant can't see or drive it.

Tenant-owned sessions (created with no user) and tenant-level callers stay
tenant-wide. Every refusal is a 404, so a session's existence isn't disclosed.
Approvals record who answered them, in the API and in the journal.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import jwt
import pytest
from httpx import ASGITransport, AsyncClient

from supervisor.agent.events import ApprovalResolved
from supervisor.app import SupervisorState, build_supervisor
from supervisor.db.engine import open_db
from supervisor.main import create_app
from tests.support.session_env import make_catalog

SECRET = "ai-service-jwt-secret-at-least-32-bytes!!"
TENANT = "t1"


def _headers(principal_id: str, tenant_id: str = TENANT) -> dict[str, str]:
    now = datetime.now(UTC)
    token = jwt.encode(
        {
            "sub": "prime-agent-bff",
            "iss": "prime-agent-api",
            "aud": "yal-supervisor",
            "type": "service",
            "tenant_id": tenant_id,
            "principal_id": principal_id,
            "iat": now,
            "exp": now + timedelta(minutes=5),
            "jti": str(uuid4()),
        },
        SECRET,
        algorithm="HS256",
    )
    return {"Authorization": f"Bearer {token}"}


ALICE, BOB, TENANT_SERVICE = _headers("alice"), _headers("bob"), _headers(TENANT)


@pytest.fixture
async def api(tmp_path: Path) -> AsyncIterator[tuple[AsyncClient, SupervisorState]]:
    async with open_db(f"sqlite+aiosqlite:///{tmp_path / 'own.db'}") as (_, factory):
        state = build_supervisor(
            session_factory=factory,
            catalog=make_catalog(),
            blob_dir=tmp_path / "blobs",
            auth_required=True,
            service_jwt_secret=SECRET,
        )
        transport = ASGITransport(app=create_app(state=state))
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            yield client, state


async def _create(client: AsyncClient, headers: dict[str, str]) -> str:
    r = await client.post(
        "/v1/sessions", json={"harness_version": "kg-v1", "runtime": "echo"}, headers=headers
    )
    assert r.status_code == 201, r.text
    return str(r.json()["session_id"])


async def _listed(client: AsyncClient, headers: dict[str, str]) -> set[str]:
    r = await client.get("/v1/sessions", headers=headers)
    assert r.status_code == 200
    return {s["session_id"] for s in r.json()}


async def test_another_user_in_the_tenant_cannot_see_or_drive_my_session(
    api: tuple[AsyncClient, SupervisorState],
) -> None:
    client, _ = api
    sid = await _create(client, ALICE)
    refused = {
        "read": await client.get(f"/v1/sessions/{sid}", headers=BOB),
        "message": await client.post(
            f"/v1/sessions/{sid}/messages", json={"text": "hi", "command_id": "c1"}, headers=BOB
        ),
        "fork": await client.post(f"/v1/sessions/{sid}/fork", json={}, headers=BOB),
        "approvals": await client.get(f"/v1/sessions/{sid}/approvals", headers=BOB),
        "delete": await client.delete(f"/v1/sessions/{sid}", headers=BOB),
    }
    assert {name: r.status_code for name, r in refused.items()} == dict.fromkeys(refused, 404)
    assert sid not in await _listed(client, BOB)
    assert (await client.get(f"/v1/sessions/{sid}", headers=ALICE)).status_code == 200


async def test_tenant_owned_sessions_and_tenant_level_callers_are_tenant_wide(
    api: tuple[AsyncClient, SupervisorState],
) -> None:
    client, _ = api
    alices = await _create(client, ALICE)
    shared = await _create(client, TENANT_SERVICE)
    assert {alices, shared} <= await _listed(client, TENANT_SERVICE)
    assert shared in await _listed(client, BOB)
    assert (await client.get(f"/v1/sessions/{shared}", headers=BOB)).status_code == 200
    assert (await client.get(f"/v1/sessions/{alices}", headers=TENANT_SERVICE)).status_code == 200


async def test_an_approval_records_who_answered_it(
    api: tuple[AsyncClient, SupervisorState],
) -> None:
    client, state = api
    sid = await _create(client, ALICE)
    approval_id = await state.manager._approvals.request(
        sid, None, "Allow the agent to run 'write'?", ["allow", "deny"], 60
    )
    assert (
        await client.post(f"/v1/approvals/{approval_id}", json={"choice": "allow"}, headers=BOB)
    ).status_code == 404
    r = await client.post(f"/v1/approvals/{approval_id}", json={"choice": "allow"}, headers=ALICE)
    assert r.status_code == 200, r.text
    assert r.json()["responded_by"] == "alice"
    journal = await state.reader.read(sid)
    resolved = [e.payload for e in journal if isinstance(e.payload, ApprovalResolved)]
    assert [r.responded_by for r in resolved] == ["alice"]
