"""D11 — BFF scope membership proxy + JWT principal_id + foreign scope 403."""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import httpx
import jwt
import pytest

from prime_agent.services.ai_client import AiClient
from tests.d6_acceptance.conftest import SECRET, as_user, make_harness, patch_ai_transport

PRIME = Path(__file__).resolve().parents[2] / "prime_agent"


def _principal_from_request(request: httpx.Request) -> str:
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return ""
    try:
        payload = jwt.decode(
            auth[7:],
            SECRET,
            algorithms=["HS256"],
            audience="yal-supervisor",
            issuer="prime-agent-api",
        )
        return str(payload.get("principal_id", ""))
    except jwt.PyJWTError:
        return ""


def _patch_workspace_ai(
    monkeypatch: pytest.MonkeyPatch,
    settings,
    handler,
) -> None:
    monkeypatch.setattr(
        "prime_agent.services.workspace_service.AiClient.from_settings",
        lambda s=None: AiClient(settings, transport=httpx.MockTransport(handler)),
    )


def test_ai_client_scope_membership_proxies() -> None:
    assert hasattr(AiClient, "list_scope_members")
    assert hasattr(AiClient, "invite_scope_member")
    assert hasattr(AiClient, "revoke_scope_member")
    import prime_agent.services.ai_client as mod

    src = Path(mod.__file__).read_text(encoding="utf-8")
    assert "/v1/scopes/" in src
    assert "/members" in src


def test_bff_routes_wire_scope_members() -> None:
    text = (PRIME / "web" / "routes" / "workspace.py").read_text(encoding="utf-8")
    assert "/scopes/{scope_id}/members" in text
    assert "invite_agent_scope_member" in text
    assert "localhost:8080" not in text


@pytest.mark.asyncio
async def test_owner_list_invite_via_bff(
    db_session, owner, d6_settings, monkeypatch
) -> None:
    harness = await make_harness(db_session, owner, bound=True)
    del harness
    scope = f"d11-{uuid4().hex[:10]}"
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        calls.append(f"{request.method} {path}")
        principal = _principal_from_request(request)
        if path.rstrip("/").endswith("/readyz"):
            return httpx.Response(
                200, json={"ready": True, "status": "ok", "run_allowed": True}
            )
        if path.endswith(f"/scopes/{scope}/members") and request.method == "GET":
            assert principal == str(owner.id)
            return httpx.Response(
                200,
                json=[
                    {
                        "principal_id": str(owner.id),
                        "grants": ["scope:admin", "workspace:read"],
                    }
                ],
            )
        if path.endswith(f"/scopes/{scope}/members") and request.method == "POST":
            assert principal == str(owner.id)
            import json

            body = json.loads(request.content.decode())
            return httpx.Response(
                201,
                json={
                    "principal_id": body["principal_id"],
                    "grants": body.get("grants") or ["workspace:read", "sandbox:exec"],
                },
            )
        return httpx.Response(404, json={"detail": "not found"})

    patch_ai_transport(monkeypatch, d6_settings, handler)
    _patch_workspace_ai(monkeypatch, d6_settings, handler)

    async with as_user(db_session, owner) as client:
        listed = await client.get(f"/api/v1/workspace/scopes/{scope}/members")
        assert listed.status_code == 200, listed.text
        assert listed.json()[0]["principal_id"] == str(owner.id)

        invite = await client.post(
            f"/api/v1/workspace/scopes/{scope}/members",
            json={"principal_id": "collab-1", "grants": ["workspace:read"]},
        )
        assert invite.status_code == 201, invite.text

    assert any("members" in c for c in calls)
    assert all("8080" not in c for c in calls)


@pytest.mark.asyncio
async def test_foreign_scope_members_403(
    db_session, owner, other_user, d6_settings, monkeypatch
) -> None:
    harness = await make_harness(db_session, owner, bound=True)
    del harness
    scope = f"d11-idor-{uuid4().hex[:10]}"

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.rstrip("/").endswith("/readyz"):
            return httpx.Response(
                200, json={"ready": True, "status": "ok", "run_allowed": True}
            )
        principal = _principal_from_request(request)
        if path.endswith(f"/scopes/{scope}/members") and request.method == "GET":
            if principal == str(other_user.id):
                return httpx.Response(403, json={"detail": "grant denied"})
            return httpx.Response(200, json=[])
        return httpx.Response(404, json={"detail": "not found"})

    patch_ai_transport(monkeypatch, d6_settings, handler)
    _patch_workspace_ai(monkeypatch, d6_settings, handler)

    async with as_user(db_session, other_user) as client:
        foreign = await client.get(f"/api/v1/workspace/scopes/{scope}/members")
        assert foreign.status_code == 403, foreign.text
