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
