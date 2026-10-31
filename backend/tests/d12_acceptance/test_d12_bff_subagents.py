"""D12 Acceptance B — the BFF proxies a session's subagents; the browser never hits the supervisor."""

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
    """AiClient reads a session's subagents from supervisor paths (never browser URLs), and
    no longer spawns one (subagents are started by the agent's own tool calls)."""
    assert hasattr(AiClient, "list_subagents") and hasattr(AiClient, "get_subagent")
    assert not hasattr(AiClient, "spawn_subagent"), "the product does not spawn subagents"

    import prime_agent.services.ai_client as mod

    src = Path(mod.__file__).read_text(encoding="utf-8")
    assert "/v1/sessions/" in src and "/subagents" in src
    assert "subagents/spawn" not in src and "/children" not in src
    # Relative supervisor paths only — never hardcoded browser supervisor hosts.
    assert "localhost:8080" not in src
    assert "http://localhost" not in src
    assert "http://127.0.0.1:8080" not in src


def test_bff_routes_wire_subagents() -> None:
    """Session routes wire the subagent list, transcript, message and stop; no host leak."""
    combined = _route_sources()
    svc = (PRIME / "services" / "session_service.py").read_text(encoding="utf-8")
    for route in (
        '"/{session_id}/subagents"',
        '"/{session_id}/subagents/{subagent_id}/events"',
        '"/{session_id}/subagents/{subagent_id}/messages"',
        '"/{session_id}/subagents/{subagent_id}/abort"',
    ):
        assert route in combined, f"BFF must wire {route}"
    assert "subagents/spawn" not in combined and "/children" not in combined
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


def _link(owner, harness, ai_sid: str) -> SessionLink:
    return SessionLink(
        id=str(uuid4()),
        user_id=owner.id,
        harness_id=harness.id,
        ai_session_id=ai_sid,
        tenant_id=str(owner.id),
        status="active",
        harness_version="githi-v1",
        runtime="echo",
        composition_id="githi-v1",
        composition_digest=harness.composition_digest,
        message_count=0,
        total_cost=0.0,
        last_seq=0,
    )


SUBAGENT = {
    "id": "child-1",
    "parent_id": "",  # set per test: the session's supervisor id
    "depth": 1,
    "label": "helper",
    "task": "summarize the workspace",
    "kind": "continuable",
    "mode": "spawn",
    "status": "ready",
    "outcome": "completed",
    "answer": "three files, one TODO",
    "answer_truncated": False,
    "open": True,
    "call_id": "call-1",
    "cost_usd": 0.002,
    "created_at": "2026-10-31T00:00:00Z",
}


@pytest.mark.asyncio
async def test_owner_reads_messages_and_stops_subagents_via_bff(
    db_session, owner, d6_settings, monkeypatch
) -> None:
    """The owner lists the subagents, reads one's transcript, messages and stops it, each
    through AiClient with a service JWT; a subagent not below the session is a 404."""
    harness = await make_harness(db_session, owner, bound=True)
    ai_sid = f"ai-d12-{uuid4().hex[:10]}"
    link = _link(owner, harness, ai_sid)
    db_session.add(link)
    await db_session.commit()
    subagent = SUBAGENT | {"parent_id": ai_sid}

    calls: list[str] = []
    upstream_tokens: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        calls.append(f"{request.method} {path}")
        if path.rstrip("/").endswith("/readyz"):
            return httpx.Response(200, json={"ready": True, "status": "ok", "run_allowed": True})
        claims = _service_claims(request)
        assert (claims.get("type"), claims.get("principal_id")) == ("service", str(owner.id))
        assert (claims.get("iss"), claims.get("aud")) == ("prime-agent-api", "yal-supervisor")
        upstream_tokens.append(request.headers["Authorization"].removeprefix("Bearer ").strip())
        match request.method, path:
            case "GET", p if p.endswith(f"/sessions/{ai_sid}/subagents"):
                return httpx.Response(200, json={"subagents": [subagent]})
            case "GET", p if p.endswith(f"/sessions/{ai_sid}/subagents/child-1"):
                return httpx.Response(200, json=subagent)
            case "GET", p if p.endswith("/sessions/child-1/events"):
                return httpx.Response(
                    200, content=b"id: 1\nevent: run_start\ndata: {}\n\n", headers={"content-type": "text/event-stream"}
                )
            case "POST", p if p.endswith("/sessions/child-1/messages"):
                body = json.loads(request.content.decode())
                assert body["text"] == "one more thing"
                return httpx.Response(202, json={"turn_id": "run-2", "status": "accepted"})
            case "POST", p if p.endswith("/sessions/child-1/abort"):
                return httpx.Response(200, json={"status": "ok"})
        return httpx.Response(404, json={"detail": "subagent not found"})

    patch_ai_transport(monkeypatch, d6_settings, handler)

    async with as_user(db_session, owner) as client:
        listed = await client.get(f"/api/v1/sessions/{link.id}/subagents")
        _fail_if_route_missing(listed, path_hint="GET /sessions/{id}/subagents")
        assert listed.status_code == 200, listed.text
        (row,) = listed.json()["subagents"]
        assert (row["id"], row["task"], row["status"], row["answer"]) == (
            "child-1", "summarize the workspace", "ready", "three files, one TODO"
        )

        streamed = await client.get(f"/api/v1/sessions/{link.id}/subagents/child-1/events")
        assert streamed.status_code == 200, streamed.text
        assert "run_start" in streamed.text

        sent = await client.post(
            f"/api/v1/sessions/{link.id}/subagents/child-1/messages", json={"content": "one more thing"}
        )
        assert sent.status_code == 202, sent.text
        assert sent.json()["turn_id"] == "run-2"

        stopped = await client.post(f"/api/v1/sessions/{link.id}/subagents/child-1/abort")
        assert stopped.status_code == 200, stopped.text

        not_below = await client.get(f"/api/v1/sessions/{link.id}/subagents/other-1/events")
        assert not_below.status_code == 404, not_below.text

        for response in (listed, streamed, sent, stopped):
            assert SECRET not in response.text and "Bearer " not in response.text
            for tok in upstream_tokens:
                assert tok not in response.text

    # Each subagent action first checks the subagent is below the session.
    assert calls.count(f"GET /v1/sessions/{ai_sid}/subagents/child-1") == 3
    assert "GET /v1/sessions/other-1/events" not in calls, "an unknown subagent is never read"
    assert all("8080" not in c for c in calls)


@pytest.mark.asyncio
async def test_foreign_session_subagents_denied(
    db_session, owner, other_user, d6_settings, monkeypatch
) -> None:
    """Another user's session → 404 at the BFF; the supervisor is never called."""
    harness = await make_harness(db_session, owner, bound=True)
    ai_sid = f"ai-d12-idor-{uuid4().hex[:8]}"
    link = _link(owner, harness, ai_sid)
    db_session.add(link)
    await db_session.commit()

    reached_supervisor = False

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal reached_supervisor
        if request.url.path.rstrip("/").endswith("/readyz"):
            return httpx.Response(200, json={"ready": True, "status": "ok", "run_allowed": True})
        reached_supervisor = True
        return httpx.Response(200, json={"subagents": [SUBAGENT | {"parent_id": ai_sid}]})

    patch_ai_transport(monkeypatch, d6_settings, handler)

    async with as_user(db_session, other_user) as client:
        for method, path, body in (
            ("GET", f"/api/v1/sessions/{link.id}/subagents", None),
            ("GET", f"/api/v1/sessions/{link.id}/subagents/child-1/events", None),
            ("POST", f"/api/v1/sessions/{link.id}/subagents/child-1/messages", {"content": "steal"}),
            ("POST", f"/api/v1/sessions/{link.id}/subagents/child-1/abort", None),
        ):
            response = await client.request(method, path, json=body)
            _fail_if_route_missing(response, path_hint=f"{method} foreign {path}")
            assert response.status_code in {403, 404}, (path, response.text)
            assert SECRET not in response.text

    assert not reached_supervisor, (
        "foreign session must be denied at BFF ownership — supervisor must not be called"
    )
