"""D9 — BFF durable workspace proxy (grants) + session scope_id bind."""

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


def test_ai_client_workspace_proxies() -> None:
    assert hasattr(AiClient, "ensure_workspace_scope")
    assert hasattr(AiClient, "list_workspace_files")
    assert hasattr(AiClient, "put_workspace_file")
    assert hasattr(AiClient, "get_workspace_file_bytes")
    assert hasattr(AiClient, "delete_workspace_file")
    assert hasattr(AiClient, "rehydrate_session_workspace")
    import prime_agent.services.ai_client as mod

    src = Path(mod.__file__).read_text(encoding="utf-8")
    assert "/v1/scopes/" in src
    assert "workspace/rehydrate" in src


def test_bff_routes_wire_workspace() -> None:
    combined = ""
    for path in (PRIME / "web" / "routes").glob("*.py"):
        combined += path.read_text(encoding="utf-8") + "\n"
    assert "/scopes/{scope_id}/ensure" in combined
    assert "/scopes/{scope_id}/files" in combined
    assert "workspace/rehydrate" in combined
    assert "localhost:8080" not in combined


def test_session_create_persists_scope_id() -> None:
    import prime_agent.services.session_service as svc

    text = Path(svc.__file__).read_text(encoding="utf-8")
    assert "scope_id=request.scope_id" in text
    assert "scope_id=link.scope_id" in text


def test_schemas_distinguish_agent_workspace_from_tenant_chrome() -> None:
    text = (PRIME / "schemas" / "workspace.py").read_text(encoding="utf-8")
    assert "Settings" in text or "org chrome" in text or "tenant" in text.lower()
    assert "sandbox" in text.lower() or "durable" in text.lower() or "supervisor" in text.lower()


@pytest.mark.asyncio
async def test_owner_put_list_via_bff(
    db_session, owner, other_user, d6_settings, monkeypatch
) -> None:
    harness = await make_harness(db_session, owner, bound=True)
    del harness  # ownership fixture only
    scope = f"d9-{uuid4().hex[:10]}"
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        calls.append(f"{request.method} {path}")
        if path.rstrip("/").endswith("/readyz"):
            return httpx.Response(
                200, json={"ready": True, "status": "ok", "run_allowed": True}
            )
        if path.endswith(f"/scopes/{scope}/ensure") and request.method == "POST":
            return httpx.Response(200, json={"scope_id": scope, "status": "ok"})
        if path.endswith(f"/scopes/{scope}/files") and request.method == "GET":
            return httpx.Response(
                200,
                json=[
                    {
                        "path": "hello.txt",
                        "content_sha256": "abc",
                        "object_key": "blobs/ab/abc",
                        "size_bytes": 5,
                        "version": 1,
                        "mime_type": "text/plain",
                        "file_id": "f1",
                    }
                ],
            )
        if "/scopes/" in path and "/files/" in path and request.method == "PUT":
            return httpx.Response(
                200,
                json={
                    "path": "hello.txt",
                    "content_sha256": "abc",
                    "object_key": "blobs/ab/abc",
                    "size_bytes": len(request.content or b""),
                    "version": 1,
                    "mime_type": "text/plain",
                    "file_id": "f1",
                },
            )
        return httpx.Response(404, json={"detail": "not found"})

    patch_ai_transport(monkeypatch, d6_settings, handler)
    monkeypatch.setattr(
        "prime_agent.services.workspace_service.AiClient.from_settings",
        lambda s=None: __import__(
            "prime_agent.services.ai_client", fromlist=["AiClient"]
        ).AiClient(d6_settings, transport=httpx.MockTransport(handler)),
    )

    async with as_user(db_session, owner) as client:
        ensure = await client.post(f"/api/v1/workspace/scopes/{scope}/ensure")
        assert ensure.status_code == 200, ensure.text
        put = await client.put(
            f"/api/v1/workspace/scopes/{scope}/files/hello.txt",
            files={"upload": ("hello.txt", b"hello", "text/plain")},
        )
        assert put.status_code == 200, put.text
        listed = await client.get(f"/api/v1/workspace/scopes/{scope}/files")
        assert listed.status_code == 200
        assert any(r["path"] == "hello.txt" for r in listed.json())

    assert any("/scopes/" in c for c in calls)
    assert all("8080" not in c for c in calls)


@pytest.mark.asyncio
async def test_t15_foreign_scope_404(
    db_session, owner, other_user, d6_settings, monkeypatch
) -> None:
    """Other tenant without workspace grant gets 404 from supervisor (BFF passes through)."""
    harness = await make_harness(db_session, owner, bound=True)
    del harness
    scope = f"d9-idor-{uuid4().hex[:10]}"

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.rstrip("/").endswith("/readyz"):
            return httpx.Response(
                200, json={"ready": True, "status": "ok", "run_allowed": True}
            )
        principal = _principal_from_request(request)
        if path.endswith(f"/scopes/{scope}/files") and request.method == "GET":
            if principal == str(other_user.id):
                return httpx.Response(404, json={"detail": "not found"})
            return httpx.Response(200, json=[])
        if path.endswith(f"/scopes/{scope}/ensure") and request.method == "POST":
            return httpx.Response(200, json={"scope_id": scope, "status": "ok"})
        return httpx.Response(404, json={"detail": "not found"})

    patch_ai_transport(monkeypatch, d6_settings, handler)
    _patch_workspace_ai(monkeypatch, d6_settings, handler)

    async with as_user(db_session, other_user) as client:
        foreign = await client.get(f"/api/v1/workspace/scopes/{scope}/files")
        assert foreign.status_code == 404, foreign.text
        assert "8080" not in foreign.text


@pytest.mark.asyncio
async def test_owner_delete_file_via_bff(
    db_session, owner, d6_settings, monkeypatch
) -> None:
    """BFF DELETE proxies durable soft-delete under grants."""
    harness = await make_harness(db_session, owner, bound=True)
    del harness
    scope = f"d9-del-{uuid4().hex[:10]}"
    deleted: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.rstrip("/").endswith("/readyz"):
            return httpx.Response(
                200, json={"ready": True, "status": "ok", "run_allowed": True}
            )
        if path.endswith(f"/scopes/{scope}/ensure") and request.method == "POST":
            return httpx.Response(200, json={"scope_id": scope, "status": "ok"})
        if "/scopes/" in path and "/files/" in path and request.method == "DELETE":
            deleted.append(path)
            return httpx.Response(204)
        return httpx.Response(404, json={"detail": "not found"})

    patch_ai_transport(monkeypatch, d6_settings, handler)
    _patch_workspace_ai(monkeypatch, d6_settings, handler)

    async with as_user(db_session, owner) as client:
        resp = await client.delete(
            f"/api/v1/workspace/scopes/{scope}/files/remove-me.txt"
        )
        assert resp.status_code == 204, resp.text

    assert any("remove-me.txt" in p for p in deleted)


@pytest.mark.asyncio
async def test_session_create_forwards_scope_id(
    db_session, owner, d6_settings, monkeypatch
) -> None:
    harness = await make_harness(db_session, owner, bound=True)
    scope = f"sess-scope-{uuid4().hex[:8]}"
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.rstrip("/").endswith("/readyz"):
            return httpx.Response(
                200, json={"ready": True, "status": "ok", "run_allowed": True}
            )
        if path.endswith("/v1/sessions") and request.method == "POST":
            import json

            seen["body"] = json.loads(request.content.decode())
            return httpx.Response(
                201,
                json={"session_id": "ai-d9-1", "status": "active"},
            )
        if "/catalog/" in path or "/compositions" in path:
            return httpx.Response(
                200,
                json={
                    "artifact_id": harness.composition_id or "githi-v1",
                    "digest": harness.composition_digest,
                    "status": "offered",
                },
            )
        return httpx.Response(404, json={"detail": "not found"})

    patch_ai_transport(monkeypatch, d6_settings, handler)

    async with as_user(db_session, owner) as client:
        resp = await client.post(
            "/api/v1/sessions",
            json={"harness_id": harness.id, "scope_id": scope},
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body.get("scope_id") == scope

    assert isinstance(seen.get("body"), dict)
    assert seen["body"].get("scope_id") == scope  # type: ignore[union-attr]
