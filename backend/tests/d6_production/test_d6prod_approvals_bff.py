"""D6 FULL-PROD §5 E — BFF approvals list/respond proxy for sessions."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from prime_agent.services.ai_client import AiClient
from tests.d6_production.conftest import as_user, make_harness, patch_ai_transport

PRIME = Path(__file__).resolve().parents[2] / "prime_agent"


def test_prod_ai_client_exposes_approvals_list_and_respond() -> None:
    """AiClient must proxy GET …/approvals and POST /v1/approvals/{id}."""
    list_names = (
        "list_approvals",
        "list_session_approvals",
        "get_session_approvals",
        "pending_approvals",
    )
    respond_names = (
        "respond_approval",
        "resolve_approval",
        "post_approval",
        "approval_respond",
    )
    has_list = any(hasattr(AiClient, n) for n in list_names)
    has_respond = any(hasattr(AiClient, n) for n in respond_names)
    assert has_list, (
        "D6 FULL-PROD: AiClient missing approvals list proxy "
        f"(expected one of {list_names})"
    )
    assert has_respond, (
        "D6 FULL-PROD: AiClient missing approvals respond proxy "
        f"(expected one of {respond_names})"
    )


def test_prod_bff_routes_or_session_service_wire_approvals() -> None:
    """BFF routes/services must expose session approvals list + respond paths."""
    combined = ""
    for path in (PRIME / "web" / "routes").glob("*.py"):
        combined += path.read_text(encoding="utf-8") + "\n"
    for path in (PRIME / "services").glob("*.py"):
        combined += path.read_text(encoding="utf-8") + "\n"

    assert "/approvals" in combined or "approvals" in combined.lower(), (
        "D6 FULL-PROD: BFF must wire approvals list/respond (UI via D4 BFF)"
    )
    # Prefer concrete path shapes.
    has_list_path = (
        "sessions/{session_id}/approvals" in combined
        or 'f"/v1/sessions/' in combined
        and "approvals" in combined
        or "/approvals" in combined
    )
    has_respond = (
        "/approvals/{approval_id}" in combined
        or "/approvals/{id}" in combined
        or "respond_approval" in combined
        or "approvals/{approval_id}" in combined
    )
    assert has_list_path and has_respond, (
        "D6 FULL-PROD: need BFF paths for GET sessions/.../approvals and "
        "POST approvals/{id} (or FE-callable equivalents)"
    )


@pytest.mark.asyncio
async def test_prod_bff_approvals_list_route_registered(
    db_session, owner, d6_settings, monkeypatch
) -> None:
    """POST/GET probe: session approvals route exists (not Starlette Not Found)."""
    await make_harness(db_session, owner, bound=True)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.rstrip("/").endswith("/readyz"):
            return httpx.Response(
                200, json={"ready": True, "status": "ok", "run_allowed": True}
            )
        if "approvals" in request.url.path:
            return httpx.Response(200, json=[])
        return httpx.Response(404, json={"detail": "unexpected"})

    patch_ai_transport(monkeypatch, d6_settings, handler)

    async with as_user(db_session, owner) as api:
        listed = await api.get("/api/v1/sessions/fake-session/approvals")
        # Alternate FE-callable path under /api/v1/approvals?session_id=
        alt = await api.get("/api/v1/approvals", params={"session_id": "fake-session"})

    def _missing(resp: httpx.Response) -> bool:
        if resp.status_code != 404:
            return False
        try:
            return resp.json().get("detail") == "Not Found"
        except Exception:
            return True

    assert not (_missing(listed) and _missing(alt)), (
        "D6 FULL-PROD: BFF has no approvals list proxy for sessions "
        f"(GET …/sessions/{{id}}/approvals → {listed.status_code}, "
        f"GET …/approvals → {alt.status_code})"
    )
