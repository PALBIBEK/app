"""D4 §5 G / T12–T15 — system BFF sessions acceptance."""

from __future__ import annotations

import json
from urllib.parse import unquote
from uuid import uuid4

import httpx
import pytest

from prime_agent.db.models.session_link import SessionLink
from prime_agent.services.auth_service import AuthService
from tests.support.catalog import PIN
from tests.d4_acceptance.conftest import (
    DIGEST,
    as_user,
    catalog_and_ready_handler,
    make_harness,
    patch_ai_transport,
)


def _error_code(response: httpx.Response) -> str | None:
    try:
        detail = response.json().get("detail")
    except Exception:
        return None
    if isinstance(detail, dict):
        return detail.get("error_code")
    return None


@pytest.mark.asyncio
async def test_t13_create_unbound_harness_returns_not_bound(
    db_session, owner, d4_settings, monkeypatch
) -> None:
    """T13: create unbound harness → BFF 422 NOT_BOUND."""
    captured = patch_ai_transport(
        monkeypatch, d4_settings, catalog_and_ready_handler(ready=True)
    )
    harness = await make_harness(db_session, owner, bound=False)

    async with as_user(db_session, owner) as api:
        r = await api.post("/api/v1/sessions", json={"harness_id": harness.id})

    assert r.status_code == 422
    assert _error_code(r) == "NOT_BOUND"
    # Must not have reached supervisor session create
    assert not any(
        req.method == "POST" and req.url.path.rstrip("/").endswith("/v1/sessions")
        for req in captured
    )


@pytest.mark.asyncio
async def test_t14_create_when_not_run_allowed_returns_ai_platform_not_ready(
    db_session, owner, d4_settings, monkeypatch
) -> None:
    """T14: create when !run_allowed → BFF 503 AI_PLATFORM_NOT_READY."""
    patch_ai_transport(
        monkeypatch, d4_settings, catalog_and_ready_handler(ready=False)
    )
    harness = await make_harness(db_session, owner, bound=True)

    async with as_user(db_session, owner) as api:
        r = await api.post("/api/v1/sessions", json={"harness_id": harness.id})

    assert r.status_code == 503
    assert _error_code(r) == "AI_PLATFORM_NOT_READY"


@pytest.mark.asyncio
async def test_t12_create_uses_pin_helper_only_and_ownership_row(
    db_session, owner, d4_settings, monkeypatch
) -> None:
    """T12 / §5 G: create inserts ownership; pins from D3 helper only (no client digests)."""
    ai_sid = f"ai-{uuid4().hex}"
    captured = patch_ai_transport(
        monkeypatch,
        d4_settings,
        catalog_and_ready_handler(ready=True, create_session_id=ai_sid),
    )
    harness = await make_harness(db_session, owner, bound=True)
    pin = PIN

    async with as_user(db_session, owner) as api:
        # Client must not be able to supply free-form digests — extra fields forbidden.
        bad = await api.post(
            "/api/v1/sessions",
            json={
                "harness_id": harness.id,
                "harness_version": "evil-v9",
                "composition_digest": "sha256:deadbeef",
            },
        )
        assert bad.status_code == 422

        r = await api.post("/api/v1/sessions", json={"harness_id": harness.id})

    assert r.status_code == 201, r.text
    body = r.json()
    assert body["harness_id"] == harness.id
    assert body["status"] == "active"
    assert body["harness_version"] == pin["harness_version"]
    assert body["composition_digest"] == DIGEST
    assert body["pin"]["composition_digest"] == DIGEST

    create_reqs = [
        req
        for req in captured
        if req.method == "POST" and req.url.path.rstrip("/").endswith("/v1/sessions")
    ]
    assert len(create_reqs) == 1
    upstream = json.loads(create_reqs[0].content.decode())
    assert upstream["harness_version"] == pin["harness_version"]
    assert upstream["runtime"] == pin["runtime"]
    assert upstream.get("composition_digest") == DIGEST
    assert "evil" not in json.dumps(upstream)

    link = await db_session.get(SessionLink, body["id"])
    assert link is not None
    assert link.user_id == owner.id
    assert link.harness_id == harness.id
    assert link.ai_session_id == ai_sid
    assert link.composition_digest == DIGEST


@pytest.mark.asyncio
async def test_t15_cross_user_session_id_returns_404(
    db_session, owner, other_user, d4_settings, monkeypatch
) -> None:
    """T15: other user’s session id → 404 on get/message/events/delete/abort."""
    ai_sid = f"ai-{uuid4().hex}"
    patch_ai_transport(
        monkeypatch,
        d4_settings,
        catalog_and_ready_handler(ready=True, create_session_id=ai_sid),
    )
    harness = await make_harness(db_session, owner, bound=True)

    async with as_user(db_session, owner) as api:
        created = await api.post("/api/v1/sessions", json={"harness_id": harness.id})
    assert created.status_code == 201
    session_id = created.json()["id"]

    async with as_user(db_session, other_user) as api:
        assert (await api.get(f"/api/v1/sessions/{session_id}")).status_code == 404
        assert (
            await api.post(
                f"/api/v1/sessions/{session_id}/messages",
                json={"content": "nope"},
            )
        ).status_code == 404
        assert (
            await api.get(f"/api/v1/sessions/{session_id}/events")
        ).status_code == 404
        assert (
            await api.post(f"/api/v1/sessions/{session_id}/abort")
        ).status_code == 404
        assert (
            await api.delete(f"/api/v1/sessions/{session_id}")
        ).status_code == 404
        assert (
            await api.post(f"/api/v1/sessions/{session_id}/fork", json={})
        ).status_code == 404


@pytest.mark.asyncio
async def test_user_jwt_never_forwarded_to_supervisor(
    db_session, owner, d4_settings, monkeypatch
) -> None:
    """§5 G / T17 posture: Authorization to supervisor is service token, not user JWT."""
    captured = patch_ai_transport(
        monkeypatch, d4_settings, catalog_and_ready_handler(ready=True)
    )
    harness = await make_harness(db_session, owner, bound=True)
    user_jwt = AuthService.create_access_token(owner.id)

    async with as_user(db_session, owner) as api:
        r = await api.post(
            "/api/v1/sessions",
            json={"harness_id": harness.id},
            headers={"Authorization": f"Bearer {user_jwt}"},
        )
    assert r.status_code == 201, r.text

    upstream_auths = [
        req.headers.get("authorization", "")
        for req in captured
        if "/v1/" in req.url.path or req.url.path.endswith("/readyz")
    ]
    assert upstream_auths
    for auth in upstream_auths:
        assert auth.startswith("Bearer ")
        assert user_jwt not in auth
        token = auth.removeprefix("Bearer ").strip()
        assert token != user_jwt


@pytest.mark.asyncio
async def test_message_mints_or_forwards_command_id(
    db_session, owner, d4_settings, monkeypatch
) -> None:
    """§5 G: message path uses command_id (BFF-minted OK); maps turn_id."""
    ai_sid = f"ai-{uuid4().hex}"
    captured = patch_ai_transport(
        monkeypatch,
        d4_settings,
        catalog_and_ready_handler(ready=True, create_session_id=ai_sid),
    )
    harness = await make_harness(db_session, owner, bound=True)

    async with as_user(db_session, owner) as api:
        created = await api.post("/api/v1/sessions", json={"harness_id": harness.id})
        assert created.status_code == 201
        sid = created.json()["id"]

        cmd = f"client-cmd-{uuid4().hex}"
        r = await api.post(
            f"/api/v1/sessions/{sid}/messages",
            json={"content": "hello", "command_id": cmd},
        )
        assert r.status_code == 202, r.text
        body = r.json()
        assert body["turn_id"]
        assert body["command_id"] == cmd
        assert body["status"] == "accepted"

        # Retry without command_id — BFF must mint one for upstream.
        r2 = await api.post(
            f"/api/v1/sessions/{sid}/messages",
            json={"content": "again"},
        )
        assert r2.status_code == 202, r2.text
        assert r2.json()["command_id"]

    msg_reqs = [
        req
        for req in captured
        if req.method == "POST" and req.url.path.endswith("/messages")
    ]
    assert len(msg_reqs) >= 2
    first_body = json.loads(msg_reqs[0].content.decode())
    assert first_body["text"] == "hello"
    assert first_body["command_id"] == cmd
    second_body = json.loads(msg_reqs[1].content.decode())
    assert second_body["command_id"]
    assert "content" not in second_body  # AI wire uses text, not content


@pytest.mark.asyncio
async def test_stop_is_abort_end_is_terminate_pause_not_faked(
    db_session, owner, d4_settings, monkeypatch
) -> None:
    """§5 G: Stop → abort; End/Delete → terminate; pause/resume not faked 200."""
    ai_sid = f"ai-{uuid4().hex}"
    captured = patch_ai_transport(
        monkeypatch,
        d4_settings,
        catalog_and_ready_handler(ready=True, create_session_id=ai_sid),
    )
    harness = await make_harness(db_session, owner, bound=True)

    async with as_user(db_session, owner) as api:
        created = await api.post("/api/v1/sessions", json={"harness_id": harness.id})
        sid = created.json()["id"]

        abort = await api.post(f"/api/v1/sessions/{sid}/abort")
        assert abort.status_code == 200
        assert abort.json().get("status") == "ok"

        pause = await api.post(f"/api/v1/sessions/{sid}/pause")
        assert pause.status_code == 501
        resume = await api.post(f"/api/v1/sessions/{sid}/resume")
        assert resume.status_code == 501

        terminate = await api.delete(f"/api/v1/sessions/{sid}")
        assert terminate.status_code == 204

    paths = [(req.method, unquote(req.url.path)) for req in captured]
    assert any(m == "POST" and p.endswith("/abort") for m, p in paths)
    assert any(m == "DELETE" and ai_sid in p for m, p in paths)
    assert not any(p.endswith("/pause") or p.endswith("/resume") for _, p in paths)


@pytest.mark.asyncio
async def test_sse_proxy_accepts_last_event_id(
    db_session, owner, d4_settings, monkeypatch
) -> None:
    """§5 G: SSE proxy with auth + Last-Event-ID forwarded upstream."""
    ai_sid = f"ai-{uuid4().hex}"
    captured = patch_ai_transport(
        monkeypatch,
        d4_settings,
        catalog_and_ready_handler(ready=True, create_session_id=ai_sid),
    )
    harness = await make_harness(db_session, owner, bound=True)

    async with as_user(db_session, owner) as api:
        created = await api.post("/api/v1/sessions", json={"harness_id": harness.id})
        sid = created.json()["id"]
        r = await api.get(
            f"/api/v1/sessions/{sid}/events",
            headers={"Last-Event-ID": "7"},
        )
    assert r.status_code == 200
    assert "text/event-stream" in r.headers.get("content-type", "")

    event_reqs = [req for req in captured if req.url.path.endswith("/events")]
    assert event_reqs
    assert event_reqs[-1].headers.get("last-event-id") == "7"


@pytest.mark.asyncio
async def test_sessions_unauthenticated_refused(db_session, d4_settings, monkeypatch) -> None:
    """BFF session APIs require user auth."""
    patch_ai_transport(monkeypatch, d4_settings, catalog_and_ready_handler())
    from prime_agent.main import create_app
    from prime_agent.web.dependencies import get_db

    app = create_app()

    async def override_db():
        yield db_session

    app.dependency_overrides[get_db] = override_db
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as api:
        r = await api.post("/api/v1/sessions", json={"harness_id": "x"})
    assert r.status_code == 401
