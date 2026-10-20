"""A6 — VK max_budget_usd enforced → 429 VIRTUAL_KEY_BUDGET_EXCEEDED."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from supervisor.gateway.app import app


@pytest.fixture()
def authed_client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("MODEL_GATEWAY_TOKEN", "secret-token")
    monkeypatch.delenv("MODEL_GATEWAY_ROUTES_JSON", raising=False)
    monkeypatch.delenv("AUTH_REQUIRED", raising=False)
    monkeypatch.delenv("ENV", raising=False)
    with TestClient(app) as c:
        yield c


def _mint(client: TestClient, *, max_budget_usd: float) -> str:
    mint = client.post(
        "/v1/virtual-keys",
        headers={"Authorization": "Bearer secret-token"},
        json={
            "tenant_id": "t1",
            "session_id": "s-budget",
            "principal_id": "p1",
            "allowed_models": ["echo"],
            "ttl_s": 120,
            "max_budget_usd": max_budget_usd,
        },
    )
    assert mint.status_code == 200, mint.text
    return mint.json()["token"]


def test_vk_budget_exceeded_after_spend(authed_client: TestClient) -> None:
    # Echo bills 0.0005/token; one chat spends 0.001, so a 0.001 budget allows exactly one.
    token = _mint(authed_client, max_budget_usd=0.001)

    ok = authed_client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {token}"},
        json={"model": "echo", "messages": [{"role": "user", "content": "one"}]},
    )
    assert ok.status_code == 200, ok.text
    usage = ok.json()["usage"]
    assert float(usage["cost_total"]) > 0

    denied = authed_client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {token}"},
        json={"model": "echo", "messages": [{"role": "user", "content": "two"}]},
    )
    assert denied.status_code == 429
    assert denied.json()["detail"]["error"]["code"] == "VIRTUAL_KEY_BUDGET_EXCEEDED"


def test_vk_zero_budget_blocks_immediately(authed_client: TestClient) -> None:
    token = _mint(authed_client, max_budget_usd=0.0)
    denied = authed_client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {token}"},
        json={"model": "echo", "messages": [{"role": "user", "content": "x"}]},
    )
    assert denied.status_code == 429
    assert denied.json()["detail"]["error"]["code"] == "VIRTUAL_KEY_BUDGET_EXCEEDED"


def test_service_token_not_budget_capped(authed_client: TestClient) -> None:
    for _ in range(3):
        r = authed_client.post(
            "/v1/chat/completions",
            headers={"Authorization": "Bearer secret-token"},
            json={"model": "echo", "messages": [{"role": "user", "content": "x"}]},
        )
        assert r.status_code == 200
