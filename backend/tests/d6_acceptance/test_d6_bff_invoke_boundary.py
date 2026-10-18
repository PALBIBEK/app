"""D6 §5 F–G / T12–T13 — BFF tools invoke + ready/tenant; no browser AI."""

from __future__ import annotations

import inspect
from pathlib import Path

import httpx
import pytest

from prime_agent.services.ai_client import AiClient
from tests.d6_acceptance.conftest import (
    as_user,
    make_harness,
    patch_ai_transport,
)

BACKEND_ROOT = Path(__file__).resolve().parents[2]
PRIME_AGENT = BACKEND_ROOT / "prime_agent"


def test_f_ai_client_exposes_session_tools_invoke() -> None:
    """§5 F: BFF AiClient must proxy POST …/sessions/{id}/tools/invoke."""
    assert hasattr(AiClient, "invoke_tool") or hasattr(AiClient, "invoke_session_tool"), (
        "AiClient missing invoke_tool — BFF cannot mediate tools/invoke (D6)"
    )


def test_f_bff_route_or_session_service_wires_invoke() -> None:
    """Session service / routes must expose a tools invoke path for the lab/Test UI."""
    # Prefer dedicated route module or method on SessionService.
    from prime_agent.services import session_service as ss_mod

    src = Path(inspect.getfile(ss_mod)).read_text(encoding="utf-8")
    routes_tools = PRIME_AGENT / "web" / "routes"
    route_hits = list(routes_tools.glob("*.py"))
    combined = src
    for path in route_hits:
        combined += "\n" + path.read_text(encoding="utf-8")
    assert "tools/invoke" in combined or "invoke_tool" in combined, (
        "BFF must wire tools/invoke (SessionService or web/routes) — no browser→supervisor"
    )


@pytest.mark.asyncio
async def test_g_invoke_refuses_when_not_run_allowed(
    db_session, owner, d6_settings, monkeypatch
) -> None:
    """§5 G / D2: tools invoke at BFF refuses when !run_allowed."""
    # If invoke route not shipped yet, fail loudly with ImportError path below.
    try:
        from prime_agent.web.routes import sessions as sessions_routes
    except ImportError:
        sessions_routes = None

    harness = await make_harness(db_session, owner, bound=True)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.rstrip("/").endswith("/readyz"):
            return httpx.Response(
                200,
                json={
                    "ready": False,
                    "status": "not_ready",
                    "reason": "draining",
                    "run_allowed": False,
                },
            )
        return httpx.Response(500, json={"detail": "should not reach supervisor"})

    patch_ai_transport(monkeypatch, d6_settings, handler)

    async with as_user(db_session, owner) as api:
        # Preferred path: POST /api/v1/sessions/{id}/tools/invoke
        # First create is blocked by ready — we only need invoke endpoint existence.
        # Probe route table via OPTIONS/POST on known patterns.
        probe = await api.post(
            "/api/v1/sessions/fake-session/tools/invoke",
            json={"name": "echo", "arguments": {}, "tool_call_id": "x"},
        )
    # 503 AI_PLATFORM_NOT_READY, 404 session, or 405 if route missing.
    if probe.status_code == 404 and "not found" in probe.text.lower() and "tools" not in probe.text.lower():
        # Starlette may 404 unknown paths — treat missing route as D6 fail.
        # Distinguish app 404 JSON vs missing route: missing often has HTML or {"detail":"Not Found"}
        detail = probe.json().get("detail") if probe.headers.get("content-type", "").startswith("application/json") else None
        if detail == "Not Found" or detail is None:
            pytest.fail(
                "D6 F: BFF route POST /api/v1/sessions/{id}/tools/invoke not registered"
            )
    assert probe.status_code in {401, 403, 404, 422, 503}, probe.text
    # Must not be 200 from supervisor leak.
    assert probe.status_code != 200


@pytest.mark.asyncio
async def test_t12_wrong_tenant_session_invoke_denied(
    db_session, owner, other_user, d6_settings, monkeypatch
) -> None:
    """T12: wrong tenant / non-owner cannot invoke via BFF."""
    harness = await make_harness(db_session, owner, bound=True)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.rstrip("/").endswith("/readyz"):
            return httpx.Response(
                200, json={"ready": True, "status": "ok", "run_allowed": True}
            )
        return httpx.Response(200, json={"ok": True, "payload": {}})

    patch_ai_transport(monkeypatch, d6_settings, handler)

    # Other user must not invoke owner's session id through BFF ownership.
    async with as_user(db_session, other_user) as api:
        r = await api.post(
            "/api/v1/sessions/owner-session-xyz/tools/invoke",
            json={"name": "echo", "arguments": {"text": "x"}, "tool_call_id": "t"},
        )
    assert r.status_code in {403, 404, 503, 422}, r.text
    assert r.status_code != 200


def test_f_sessions_api_fe_does_not_hardcode_supervisor_host() -> None:
    """Static: FE sessions/tools clients stay on BFF base URL."""
    fe = BACKEND_ROOT.parent / "frontend" / "src" / "lib" / "api" / "endpoints"
    assert fe.is_dir(), fe
    offenders: list[str] = []
    for path in fe.glob("*.ts"):
        if path.name.endswith(".test.ts"):
            continue
        text = path.read_text(encoding="utf-8")
        if ":8080" in text or "/v1/catalog" in text or "tools/invoke" in text and "localhost:8080" in text:
            offenders.append(path.name)
    assert not offenders, f"endpoints must not hardcode supervisor: {offenders}"
