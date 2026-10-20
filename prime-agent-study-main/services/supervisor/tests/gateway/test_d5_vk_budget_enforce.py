"""D5 §6 A / T7 — VK max_budget_usd must enforce or be removed from mint claims."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from supervisor.gateway.app import app
from supervisor.gateway.config import Settings
from supervisor.gateway.factory import build_provider_registry
from supervisor.gateway.virtual_keys import MemoryVirtualKeyStore

SERVICE = "d5-vk-budget-svc"

_ROUTES = {
    "echo": {"provider": "echo", "upstream_model": "echo"},
}


def _configure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MODEL_GATEWAY_TOKEN", SERVICE)
    monkeypatch.setenv("ENV", "development")
    monkeypatch.delenv("AUTH_REQUIRED", raising=False)
    monkeypatch.setenv("MODEL_GATEWAY_ROUTES_JSON", json.dumps(_ROUTES))
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    settings = Settings()
    app.state.settings = settings
    app.state.registry = build_provider_registry(settings)
    app.state.virtual_keys = MemoryVirtualKeyStore()


def test_d5_vk_budget_enforced_or_claim_removed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Focus #5: if mint advertises max_budget_usd, overspend is refused (429 VIRTUAL_KEY_BUDGET_EXCEEDED,
    the status the supervisor treats as a spent budget).

    Soft code-inspection is not enough — D5 requires runtime enforcement when
    the claim surface still exists.
    """
    _configure(monkeypatch)
    with TestClient(app) as client:
        _configure(monkeypatch)
        mint = client.post(
            "/v1/virtual-keys",
            headers={"Authorization": f"Bearer {SERVICE}"},
            json={
                "tenant_id": "t",
                "session_id": "s-budget",
                "principal_id": "p",
                "allowed_models": ["echo"],
                "ttl_s": 3600,
                "max_budget_usd": 0.0,
            },
        )
        assert mint.status_code == 200, mint.text
        body = mint.json()
        if "max_budget_usd" not in body:
            pytest.skip(
                "max_budget_usd removed from mint claims — supervisor-owned budget OK"
            )

        vk = body["token"]
        assert vk.startswith("vk_")
        statuses: list[int] = []
        codes: list[str] = []
        for _ in range(4):
            r = client.post(
                "/v1/chat/completions",
                headers={"Authorization": f"Bearer {vk}"},
                json={
                    "model": "echo",
                    "messages": [{"role": "user", "content": "spend"}],
                },
            )
            statuses.append(r.status_code)
            detail = r.json().get("detail") or {}
            err = detail.get("error") if isinstance(detail, dict) else {}
            if isinstance(err, dict) and err.get("code"):
                codes.append(str(err["code"]))
            if r.status_code == 429:
                break

        assert 429 in statuses, (
            "max_budget_usd advertised but never enforced: "
            f"statuses={statuses} codes={codes}"
        )
        assert "VIRTUAL_KEY_BUDGET_EXCEEDED" in codes
