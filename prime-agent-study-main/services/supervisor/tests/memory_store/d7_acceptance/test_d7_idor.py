"""D7 §5 A / T2 — tenant-safe item get (no IDOR)."""

from __future__ import annotations

from starlette.testclient import TestClient

from .conftest import headers


def test_t2_cross_tenant_get_item_denied(authed: TestClient) -> None:
    """T2: GET item for another tenant → 404 or 403 (never 200 with content)."""
    created = authed.post(
        "/v1/memory/items",
        headers=headers("d7-tenant-a"),
        json={
            "scope": {"tenant": "d7-tenant-a", "subject": "s", "namespace": "n"},
            "content": "owned by A only",
        },
    )
    assert created.status_code == 201, created.text
    item_id = created.json()["id"]

    ok = authed.get(f"/v1/memory/items/{item_id}", headers=headers("d7-tenant-a"))
    assert ok.status_code == 200
    assert "owned by A" in ok.json().get("content", "")

    leak = authed.get(f"/v1/memory/items/{item_id}", headers=headers("d7-tenant-b"))
    assert leak.status_code in {403, 404}, leak.text
    if leak.headers.get("content-type", "").startswith("application/json"):
        body = leak.json()
        # Must not echo foreign content.
        assert "owned by A" not in str(body)
