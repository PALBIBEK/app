"""Workspace uploads: the browser sends bytes to storage itself; the API only starts and completes."""

from __future__ import annotations

import json
from types import SimpleNamespace

import httpx

from prime_agent.config import Settings
from prime_agent.main import create_app
from prime_agent.schemas.ai_identity import TenantPrincipal
from prime_agent.services.ai_client import AiClient
from prime_agent.services.workspace_service import WorkspaceService
from prime_agent.web.dependencies import get_current_user, get_db

SHA = "AB" * 32
SETTINGS = Settings(
    environment="development",
    ai_supervisor_base_url="http://supervisor.test",
    ai_service_jwt_secret="ai-service-jwt-secret-at-least-32-bytes!!",
    workspace_max_upload_bytes=8,
)
FILE = {"path": "a.txt", "content_sha256": SHA.lower(), "object_key": "blobs/ab/x", "size_bytes": 3,
        "version": 1, "mime_type": "text/plain", "file_id": "f1"}


def _api(db, user, handler, monkeypatch) -> httpx.AsyncClient:
    client = AiClient(SETTINGS, transport=httpx.MockTransport(handler))
    monkeypatch.setattr("prime_agent.web.routes.workspace._service", lambda: WorkspaceService(SETTINGS, client=client))
    monkeypatch.setattr("prime_agent.web.routes.workspace.get_settings", lambda: SETTINGS)
    app = create_app()

    async def override_db():
        yield db

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = lambda: user
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def test_start_and_complete_go_to_the_supervisor_as_the_user(db_session, make_user, monkeypatch):
    user = await make_user(email="up@example.com", name="Up")
    seen: list[tuple[str, dict]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        seen.append((request.url.path, body))
        if request.url.path.endswith("/uploads"):
            return httpx.Response(200, json={
                "status": "upload", "max_bytes": 100, "upload_token": "tok", "url": "http://s3.test/u",
                "method": "PUT", "headers": {"x-amz-checksum-sha256": "q8="}, "expires_in_s": 900, "file": None,
            })
        return httpx.Response(200, json=FILE)

    async with _api(db_session, user, handler, monkeypatch) as api:
        started = await api.post("/api/v1/workspace/scopes/s1/uploads",
                                 json={"path": "a.txt", "size_bytes": 3, "content_sha256": SHA, "mime_type": "text/plain"})
        done = await api.post("/api/v1/workspace/scopes/s1/uploads/complete", json={"upload_token": "tok"})
    assert started.status_code == 200, started.text
    assert started.json()["url"] == "http://s3.test/u" and started.json()["upload_token"] == "tok"
    assert seen[0] == ("/v1/scopes/s1/uploads",
                       {"path": "a.txt", "size_bytes": 3, "content_sha256": SHA.lower(), "mime_type": "text/plain"})
    assert seen[1] == ("/v1/scopes/s1/uploads/complete", {"upload_token": "tok"})
    assert done.json()["path"] == "a.txt"


async def test_the_supervisors_refusal_reaches_the_browser(db_session, make_user, monkeypatch):
    user = await make_user(email="up2@example.com", name="Up")
    reason = "file is 999 bytes; this workspace takes files up to 100 bytes"
    handler = lambda r: httpx.Response(413, json={"detail": reason})  # noqa: E731
    async with _api(db_session, user, handler, monkeypatch) as api:
        r = await api.post("/api/v1/workspace/scopes/s1/uploads",
                           json={"path": "a.txt", "size_bytes": 999, "content_sha256": SHA})
    assert r.status_code == 413 and r.json()["detail"]["message"] == reason


async def test_the_pass_through_upload_stops_at_the_limit_without_calling_the_supervisor(db_session, make_user, monkeypatch):
    user = await make_user(email="up3@example.com", name="Up")
    calls: list[httpx.Request] = []
    async with _api(db_session, user, lambda r: calls.append(r) or httpx.Response(200, json=FILE), monkeypatch) as api:
        big = await api.put("/api/v1/workspace/scopes/s1/files/big.bin", files={"upload": ("big.bin", b"x" * 9)})
        small = await api.put("/api/v1/workspace/scopes/s1/files/a.txt", files={"upload": ("a.txt", b"abc")})
    assert big.status_code == 413 and "up to 8 bytes" in big.json()["detail"]["message"]
    assert small.status_code == 200 and len(calls) == 1 and calls[0].content == b"abc"


async def test_refreshing_a_session_without_a_sandbox_yet_is_not_an_error(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(409, json={"detail": "session has no sandbox to rehydrate"})

    service = WorkspaceService(SETTINGS, client=AiClient(SETTINGS, transport=httpx.MockTransport(handler)))

    async def owned(*_args):
        return SimpleNamespace(scope_id="s1", ai_session_id="ai-1")

    monkeypatch.setattr(service, "_owned_session", owned)
    identity = TenantPrincipal(tenant_id="t", principal_id="p")
    out = await service.rehydrate_session(session_id="s", user_id="u", identity=identity, db=None)
    assert out.status == "no_sandbox"


async def test_history_names_authors_by_email_and_restore_goes_to_the_supervisor(db_session, make_user, monkeypatch):
    user = await make_user(email="hist@example.com", name="Hist")
    seen: list[tuple[str, str, bytes]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.raw_path.decode(), request.content))
        path = request.url.path
        if path.endswith("/@1"):
            return httpx.Response(200, content=b"first")
        if "/history/" in path:
            return httpx.Response(200, json=[
                {"version": 2, "content_sha256": "b" * 64, "size_bytes": 6, "mime_type": "text/plain",
                 "created_at": "2026-10-22T00:00:00+00:00", "author": user.id, "restored_from": None, "current": True},
                {"version": 1, "content_sha256": "a" * 64, "size_bytes": 5, "mime_type": "text/plain",
                 "created_at": "2026-10-21T00:00:00+00:00", "author": "agent-run", "restored_from": None, "current": False},
            ])
        return httpx.Response(200, json={**FILE, "path": "notes/my plan.md", "version": 3})

    async with _api(db_session, user, handler, monkeypatch) as api:
        history = await api.get("/api/v1/workspace/scopes/s1/history/notes/my%20plan.md")
        old = await api.get("/api/v1/workspace/scopes/s1/history/notes/my%20plan.md/@1")
        restored = await api.post("/api/v1/workspace/scopes/s1/restore/notes/my%20plan.md", json={"version": 1})
    assert history.status_code == 200, history.text
    assert [(v["version"], v["author_email"]) for v in history.json()] == [(2, "hist@example.com"), (1, None)]
    assert old.content == b"first"
    assert restored.json()["version"] == 3
    assert [s[1] for s in seen] == [
        "/v1/scopes/s1/history/notes/my%20plan.md",
        "/v1/scopes/s1/history/notes/my%20plan.md/@1",
        "/v1/scopes/s1/restore/notes/my%20plan.md",
    ]
    assert json.loads(seen[2][2]) == {"version": 1}


async def _link(db, owner):
    from datetime import UTC, datetime
    from uuid import uuid4

    from prime_agent.db.models.session_link import SessionLink
    from prime_agent.services.harness_service import HarnessService

    harness = await HarnessService.acreate(
        {"name": f"h-{uuid4().hex[:6]}", "model": "gateway/echo", "mode": "sticky", "user_id": owner.id, "created_by": owner.id}, db
    )
    link = SessionLink(
        id=str(uuid4()), user_id=owner.id, harness_id=harness.id, ai_session_id="ai-files-1", tenant_id=owner.id,
        harness_version="h-r1", runtime="githi", status="active", scope_id="ws-1",
        created_at=datetime.now(UTC), updated_at=datetime.now(UTC),
    )
    db.add(link)
    await db.flush()
    return link


async def test_attachments_and_artifacts_go_to_the_owners_session_only(db_session, make_user, monkeypatch):
    owner = await make_user(email="files@example.com", name="Files")
    other = await make_user(email="other-files@example.com", name="Other")
    link = await _link(db_session, owner)
    seen: list[tuple[str, str, bytes]] = []
    artifact = {"id": "a1", "turn_id": "t1", "name": "report.md", "content_sha256": "c" * 64, "size_bytes": 4,
                "mime_type": "text/markdown", "created_at": "2026-10-22T00:00:00+00:00",
                "saved_scope_id": None, "saved_path": None, "saved_version": None}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path, request.content))
        path = request.url.path
        if path.endswith("/attachments") and request.method == "POST":
            return httpx.Response(201, json={"id": "att1", "scope_id": "ws-1", "path": "data/sales.csv", "version": 2,
                                             "content_sha256": "d" * 64, "size_bytes": 6, "mime_type": "text/csv",
                                             "name": "sales.csv", "sandbox_path": ".attachments/sales.csv",
                                             "attached_by": owner.id, "created_at": "2026-10-22T00:00:00+00:00"})
        if path.endswith("/artifacts"):
            return httpx.Response(200, json=[artifact])
        if path.endswith("/content"):
            return httpx.Response(200, content=b"# Q4")
        if path.endswith("/save"):
            return httpx.Response(200, json={"artifact": {**artifact, "saved_path": "reports/q4.md", "saved_version": 1,
                                                          "saved_scope_id": "ws-1"},
                                             "path": "reports/q4.md", "version": 1, "scope_id": "ws-1"})
        if path.endswith("/inputs"):
            return httpx.Response(200, json=[{"kind": "attachment", "scope_id": "ws-1", "path": "data/sales.csv",
                                              "version": 2, "content_sha256": "d" * 64}])
        return httpx.Response(204)

    base = f"/api/v1/workspace/sessions/{link.id}"
    async with _api(db_session, owner, handler, monkeypatch) as api:
        attached = await api.post(f"{base}/attachments", json={"path": "data/sales.csv"})
        listed = await api.get(f"{base}/artifacts")
        content = await api.get(f"{base}/artifacts/a1/content")
        saved = await api.post(f"{base}/artifacts/a1/save", json={"path": "reports/q4.md"})
        inputs = await api.get(f"{base}/runs/t1/inputs")
        detached = await api.delete(f"{base}/attachments/att1")
    assert attached.status_code == 201, attached.text
    assert attached.json()["sandbox_path"] == ".attachments/sales.csv"
    assert listed.json()[0]["name"] == "report.md" and content.content == b"# Q4"
    assert saved.json()["path"] == "reports/q4.md" and saved.json()["artifact"]["saved_version"] == 1
    assert inputs.json()[0]["kind"] == "attachment" and detached.status_code == 204
    assert [(m, p) for m, p, _ in seen] == [
        ("POST", "/v1/sessions/ai-files-1/attachments"),
        ("GET", "/v1/sessions/ai-files-1/artifacts"),
        ("GET", "/v1/sessions/ai-files-1/artifacts/a1/content"),
        ("POST", "/v1/sessions/ai-files-1/artifacts/a1/save"),
        ("GET", "/v1/sessions/ai-files-1/runs/t1/inputs"),
        ("DELETE", "/v1/sessions/ai-files-1/attachments/att1"),
    ]
    assert json.loads(seen[0][2]) == {"path": "data/sales.csv"}

    calls_before = len(seen)
    async with _api(db_session, other, handler, monkeypatch) as api:
        theirs = await api.get(f"{base}/artifacts")
    assert theirs.status_code == 404 and len(seen) == calls_before  # refused before the supervisor
