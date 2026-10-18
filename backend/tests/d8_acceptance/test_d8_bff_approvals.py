"""D8 T15 — BFF approvals list/respond + 409 mapping (no browser→supervisor)."""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import httpx
import pytest

from prime_agent.db.models.session_link import SessionLink
from prime_agent.services.ai_client import AiClient
from tests.d6_production.conftest import as_user, make_harness, patch_ai_transport

PRIME = Path(__file__).resolve().parents[2] / "prime_agent"


def test_t15_ai_client_approvals_proxies() -> None:
    assert hasattr(AiClient, "list_session_approvals")
    assert hasattr(AiClient, "respond_approval")
    text = Path(AiClient.__module__.replace("prime_agent.", "").replace(".", "/") )
    # Read from installed module path
    import prime_agent.services.ai_client as mod

    src = Path(mod.__file__).read_text(encoding="utf-8")
    assert "/approvals" in src
    assert "respond_approval" in src


def test_t15_bff_routes_wire_approvals() -> None:
    combined = ""
    for path in (PRIME / "web" / "routes").glob("*.py"):
        combined += path.read_text(encoding="utf-8") + "\n"
    assert "/{session_id}/approvals" in combined
    assert "respond_session_approval" in combined or "respond_approval" in combined


def test_t15_409_mapping_for_awaiting_and_approval_errors() -> None:
    import prime_agent.services.session_service as svc

    text = Path(svc.__file__).read_text(encoding="utf-8")
    assert "SESSION_AWAITING_APPROVAL" in text
    assert "INVALID_APPROVAL_CHOICE" in text or "422" in text


@pytest.mark.asyncio
async def test_t15_list_respond_as_owner(
    db_session, owner, other_user, d6_settings, monkeypatch
) -> None:
    """Owner can list/respond; wrong owner gets 404 (not supervisor leak)."""
    harness = await make_harness(db_session, owner, bound=True)
    link = SessionLink(
        id=str(uuid4()),
        user_id=owner.id,
        harness_id=harness.id,
        ai_session_id="ai-sess-d8",
        tenant_id=str(owner.id),
        status="awaiting_approval",
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

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        calls.append(f"{request.method} {path}")
        if path.rstrip("/").endswith("/readyz"):
            return httpx.Response(
                200, json={"ready": True, "status": "ok", "run_allowed": True}
            )
        if path.endswith("/approvals") and request.method == "GET":
            return httpx.Response(
                200,
                json=[
                    {
                        "approval_id": "appr-1",
                        "session_id": "ai-sess-d8",
                        "prompt": "allow?",
                        "options": ["approve", "deny"],
                        "status": "pending",
                        "choice": None,
                        "timeout_s": 300,
                    }
                ],
            )
        if "/approvals/" in path and request.method == "POST":
            return httpx.Response(
                200,
                json={
                    "approval_id": "appr-1",
                    "session_id": "ai-sess-d8",
                    "prompt": "allow?",
                    "options": ["approve", "deny"],
                    "status": "responded",
                    "choice": "approve",
                    "timeout_s": 300,
                },
            )
        if path.endswith("/messages") and request.method == "POST":
            return httpx.Response(409, json={"detail": "awaiting approval"})
        return httpx.Response(404, json={"detail": "unexpected"})

    patch_ai_transport(monkeypatch, d6_settings, handler)

    async with as_user(db_session, owner) as api:
        listed = await api.get(f"/api/v1/sessions/{link.id}/approvals")
        assert listed.status_code == 200, listed.text
        assert listed.json()[0]["approval_id"] == "appr-1"

        responded = await api.post(
            f"/api/v1/sessions/{link.id}/approvals/appr-1",
            json={"choice": "approve"},
        )
        assert responded.status_code == 200, responded.text
        assert responded.json()["status"] == "responded"

        # 409 while awaiting — mapped through BFF
        conflict = await api.post(
            f"/api/v1/sessions/{link.id}/messages",
            json={"content": "hi"},
        )
        assert conflict.status_code == 409, conflict.text

    async with as_user(db_session, other_user) as api:
        stolen = await api.get(f"/api/v1/sessions/{link.id}/approvals")
        assert stolen.status_code == 404, stolen.text
