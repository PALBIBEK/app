"""D8 — BFF maps supervisor 429 on post_message to BUDGET_EXCEEDED."""

from __future__ import annotations

from uuid import uuid4

import httpx
import pytest

from prime_agent.db.models.session_link import SessionLink
from tests.d6_production.conftest import as_user, make_harness, patch_ai_transport


@pytest.mark.asyncio
async def test_post_message_429_maps_budget_exceeded(
    db_session, owner, d6_settings, monkeypatch
) -> None:
    """Supervisor budget refusal (429) on post_message → BFF error_code BUDGET_EXCEEDED."""
    harness = await make_harness(db_session, owner, bound=True)
    link = SessionLink(
        id=str(uuid4()),
        user_id=owner.id,
        harness_id=harness.id,
        ai_session_id="ai-sess-d8-budget",
        tenant_id=str(owner.id),
        status="active",
        harness_version="coding-v1",
        runtime="echo",
        composition_id="coding-v1",
        composition_digest=harness.composition_digest,
        message_count=0,
        total_cost=4.99,
        last_seq=0,
    )
    db_session.add(link)
    await db_session.commit()

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.rstrip("/").endswith("/readyz"):
            return httpx.Response(
                200, json={"ready": True, "status": "ok", "run_allowed": True}
            )
        if path.endswith("/messages") and request.method == "POST":
            return httpx.Response(429, json={"detail": "session budget exceeded"})
        return httpx.Response(404, json={"detail": "unexpected"})

    patch_ai_transport(monkeypatch, d6_settings, handler)

    async with as_user(db_session, owner) as api:
        resp = await api.post(
            f"/api/v1/sessions/{link.id}/messages",
            json={"content": "one more turn"},
        )
        assert resp.status_code == 429, resp.text
        detail = resp.json()["detail"]
        assert detail["error_code"] == "BUDGET_EXCEEDED"
