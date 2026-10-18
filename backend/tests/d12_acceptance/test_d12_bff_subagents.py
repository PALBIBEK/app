"""D12 Acceptance B — BFF proxies spawn + list children; browser never hits supervisor."""

from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

import httpx
import jwt
import pytest

from prime_agent.db.models.session_link import SessionLink
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


def _service_claims(request: httpx.Request) -> dict:
    auth = request.headers.get("Authorization", "")
    assert auth.startswith("Bearer "), f"missing service Bearer: {auth!r}"
    return jwt.decode(
        auth[7:],
        SECRET,
        algorithms=["HS256"],
        audience="yal-supervisor",
        issuer="prime-agent-api",
    )


def _route_sources() -> str:
    combined = ""
    for path in sorted((PRIME / "web" / "routes").glob("*.py")):
        combined += path.read_text(encoding="utf-8") + "\n"
    return combined


def _fail_if_route_missing(resp: httpx.Response, *, path_hint: str) -> None:
    """Starlette unknown-path 404 must not count as ownership denial."""
    if resp.status_code != 404:
        return
    if not resp.headers.get("content-type", "").startswith("application/json"):
        pytest.fail(f"D12 B: BFF route missing for {path_hint}: {resp.text}")
    detail = resp.json().get("detail")
    if detail == "Not Found":
        pytest.fail(f"D12 B: BFF route not registered for {path_hint}")


def test_ai_client_subagent_proxies() -> None:
    """AiClient must expose spawn + list-children against supervisor paths (not browser URLs)."""
    has_spawn = (
        hasattr(AiClient, "spawn_subagent")
        or hasattr(AiClient, "spawn_session_subagent")
        or hasattr(AiClient, "spawn_child")
    )
    has_list = (
        hasattr(AiClient, "list_session_children")
        or hasattr(AiClient, "list_children")
        or hasattr(AiClient, "list_session_subagents")
    )
    assert has_spawn, (
        "AiClient missing spawn_subagent (or equivalent) — BFF cannot proxy spawn (D12 B)"
    )
    assert has_list, (
        "AiClient missing list_session_children (or equivalent) — "
        "BFF cannot proxy children (D12 B)"
    )

    import prime_agent.services.ai_client as mod

    src = Path(mod.__file__).read_text(encoding="utf-8")
    has_sessions_prefix = "/v1/sessions/" in src
    has_spawn_path = "subagents/spawn" in src
    has_children_path = "/children" in src
    assert has_sessions_prefix, "AiClient must use supervisor /v1/sessions/ paths"
    assert has_spawn_path, "AiClient source must contain subagents/spawn"
    assert has_children_path, "AiClient source must contain /children"
    # Relative supervisor paths only — never hardcoded browser supervisor hosts.
    assert "localhost:8080" not in src
    assert "http://localhost" not in src
    assert "http://127.0.0.1:8080" not in src


def test_bff_routes_wire_subagents() -> None:
    """Sessions (or dedicated) routes must wire spawn + children; no supervisor host leak."""
    combined = _route_sources()
    svc = (PRIME / "services" / "session_service.py").read_text(encoding="utf-8")

    wired_spawn = "subagents/spawn" in combined or (
        "spawn_subagent" in combined and "subagents" in combined
    )
    wired_spawn = wired_spawn or ("spawn_subagent" in svc and "subagents/spawn" in svc)
    wired_children = "/{session_id}/children" in combined or (
        "list_session_children" in combined or "list_children" in svc
    )
    assert wired_spawn, "BFF must wire subagents/spawn (routes or SessionService)"
    assert wired_children, "BFF must wire GET …/children (routes or SessionService)"
    assert "localhost:8080" not in combined
    assert "localhost:8080" not in svc


def test_bff_does_not_expose_supervisor_token_in_sources() -> None:
    """Static: BFF response schemas / routes must not surface supervisor JWT material."""
    forbidden_fields = (
        "ai_service_jwt_secret",
        "service_jwt",
        "supervisor_token",
        "ai_service_token",
    )
    # Response models / session + workspace routes — no token fields for clients.
    for path in (PRIME / "schemas").glob("*.py"):
        text = path.read_text(encoding="utf-8")
        for field in forbidden_fields:
            assert field not in text, f"{path.name} must not declare {field}"

    for path in (PRIME / "web" / "routes").glob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert "ai_service_jwt_secret" not in text, (
            f"{path.name} must not reference ai_service_jwt_secret"
        )
        # Must not hardcode supervisor browser URL for child APIs.
        assert "localhost:8080" not in text

    svc = (PRIME / "services" / "session_service.py").read_text(encoding="utf-8")
    # SessionService must never hold/return the signing secret (minting stays in AiClient).
    assert "ai_service_jwt_secret" not in svc
    assert "localhost:8080" not in svc


@pytest.mark.asyncio
async def test_owner_list_spawn_via_bff(
    db_session, owner, d6_settings, monkeypatch
) -> None:
    """Authenticated owner: list children + spawn via BFF → AiClient with service JWT."""
    harness = await make_harness(db_session, owner, bound=True)
    ai_sid = f"ai-d12-{uuid4().hex[:10]}"
    link = SessionLink(
        id=str(uuid4()),
        user_id=owner.id,
        harness_id=harness.id,
        ai_session_id=ai_sid,
        tenant_id=str(owner.id),
        status="active",
        harness_version="coding-v1",
        runtime="echo",
        composition_id="coding-v1",
        composition_digest=harness.composition_digest,
        message_count=0,
        total_cost=0.0,
        last_seq=0,
    )
    db_session.add(link)
    await db_session.commit()

    calls: list[str] = []
    upstream_tokens: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        calls.append(f"{request.method} {path}")
        if path.rstrip("/").endswith("/readyz"):
            return httpx.Response(
                200, json={"ready": True, "status": "ok", "run_allowed": True}
            )
        claims = _service_claims(request)
        assert claims.get("type") == "service"
        assert claims.get("principal_id") == str(owner.id)
        assert claims.get("iss") == "prime-agent-api"
        assert claims.get("aud") == "yal-supervisor"
        upstream_tokens.append(
            request.headers["Authorization"].removeprefix("Bearer ").strip()
        )

        if path.endswith(f"/sessions/{ai_sid}/children") and request.method == "GET":
            assert _principal_from_request(request) == str(owner.id)
            return httpx.Response(
                200,
                json=[
                    {
                        "id": "child-1",
                        "mode": "spawn",
                        "provider": "memory",
                        "parent_id": ai_sid,
                        "run_id": "run-1",
                        "label": "lab child",
                        "activity": "running",
                        "depth": 1,
                    }
                ],
            )
        if (
            path.endswith(f"/sessions/{ai_sid}/subagents/spawn")
            and request.method == "POST"
        ):
            assert _principal_from_request(request) == str(owner.id)
            body = json.loads(request.content.decode())
            assert body.get("prompt")
            return httpx.Response(
                200,
                json={
                    "child_session_id": "child-spawned-1",
                    "text": "ok",
                    "status": "completed",
                    "run_id": "run-spawn-1",
                },
            )
        return httpx.Response(404, json={"detail": "not found"})

    patch_ai_transport(monkeypatch, d6_settings, handler)

    async with as_user(db_session, owner) as client:
        listed = await client.get(f"/api/v1/sessions/{link.id}/children")
        _fail_if_route_missing(listed, path_hint="GET /sessions/{id}/children")
        assert listed.status_code == 200, listed.text
        assert listed.json()[0]["id"] == "child-1"
        # BFF must not echo supervisor service JWT / signing secret to the browser.
        assert SECRET not in listed.text
        assert "Bearer " not in listed.text
        for tok in upstream_tokens:
            assert tok not in listed.text

        spawned = await client.post(
            f"/api/v1/sessions/{link.id}/subagents/spawn",
            json={"prompt": "summarize the workspace", "provider": "lab_echo"},
        )
        _fail_if_route_missing(
            spawned, path_hint="POST /sessions/{id}/subagents/spawn"
        )
        assert spawned.status_code in {200, 201}, spawned.text
        body = spawned.json()
        assert (
            body.get("child_session_id") == "child-spawned-1"
            or body.get("id") == "child-spawned-1"
        )
        assert SECRET not in spawned.text
        assert "Bearer " not in spawned.text
        for tok in upstream_tokens:
            assert tok not in spawned.text

    assert any("/children" in c for c in calls)
    assert any("subagents/spawn" in c for c in calls)
    assert all("8080" not in c for c in calls)
    assert upstream_tokens, "AiClient must mint service JWT toward supervisor"


@pytest.mark.asyncio
async def test_foreign_session_subagents_denied(
    db_session, owner, other_user, d6_settings, monkeypatch
) -> None:
    """Foreign harness/session owner → 404/403; never 200 from supervisor leak."""
    harness = await make_harness(db_session, owner, bound=True)
    ai_sid = f"ai-d12-idor-{uuid4().hex[:8]}"
    link = SessionLink(
        id=str(uuid4()),
        user_id=owner.id,
        harness_id=harness.id,
        ai_session_id=ai_sid,
        tenant_id=str(owner.id),
        status="active",
        harness_version="coding-v1",
        runtime="echo",
        composition_id="coding-v1",
        composition_digest=harness.composition_digest,
        message_count=0,
        total_cost=0.0,
        last_seq=0,
    )
    db_session.add(link)
    await db_session.commit()

    reached_supervisor = False

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal reached_supervisor
        path = request.url.path
        if path.rstrip("/").endswith("/readyz"):
            return httpx.Response(
                200, json={"ready": True, "status": "ok", "run_allowed": True}
            )
        if f"/sessions/{ai_sid}/" in path or path.endswith(
            f"/sessions/{ai_sid}/children"
        ):
            reached_supervisor = True
            return httpx.Response(200, json=[{"id": "leaked"}])
        return httpx.Response(404, json={"detail": "not found"})

    patch_ai_transport(monkeypatch, d6_settings, handler)

    async with as_user(db_session, other_user) as client:
        stolen_list = await client.get(f"/api/v1/sessions/{link.id}/children")
        _fail_if_route_missing(stolen_list, path_hint="GET foreign /children")
        assert stolen_list.status_code in {403, 404}, stolen_list.text
        assert stolen_list.status_code != 200
        assert SECRET not in stolen_list.text

        stolen_spawn = await client.post(
            f"/api/v1/sessions/{link.id}/subagents/spawn",
            json={"prompt": "steal", "provider": "lab_echo"},
        )
        _fail_if_route_missing(
            stolen_spawn, path_hint="POST foreign /subagents/spawn"
        )
        assert stolen_spawn.status_code in {403, 404}, stolen_spawn.text
        assert stolen_spawn.status_code != 200
        assert SECRET not in stolen_spawn.text

    assert not reached_supervisor, (
        "foreign session must be denied at BFF ownership — supervisor must not be called"
    )
