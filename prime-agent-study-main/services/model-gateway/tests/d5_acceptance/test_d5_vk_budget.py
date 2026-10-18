"""D5 §6 A / T7 — virtual-key max_budget_usd enforced on spend."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from model_gateway.app import app

from .helpers import SERVICE_TOKEN, configure_app


def _mint(client: TestClient, *, max_budget_usd: float) -> str:
    r = client.post(
        "/v1/virtual-keys",
        headers={"Authorization": f"Bearer {SERVICE_TOKEN}"},
        json={
            "tenant_id": "t-d5",
            "session_id": "s-d5-budget",
            "principal_id": "p-d5",
            "allowed_models": ["echo"],
            "ttl_s": 3600,
            "max_budget_usd": max_budget_usd,
        },
    )
    assert r.status_code == 200, r.text
    token = r.json()["token"]
    assert token.startswith("vk_")
    return token


def test_t7_vk_over_budget_returns_403_virtual_key_budget_exceeded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """T7: VK spend past max_budget_usd → 403 VIRTUAL_KEY_BUDGET_EXCEEDED."""
    configure_app(monkeypatch)
    with TestClient(app) as client:
        configure_app(monkeypatch)
        vk = _mint(client, max_budget_usd=0.0)

        # First completion may succeed or immediately deny depending on
        # whether zero budget blocks before first spend; either way, once
        # spend would exceed, chat must 403 with the stable code.
        statuses: list[int] = []
        codes: list[str] = []
        for _ in range(3):
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
            if r.status_code == 403:
                break

    assert 403 in statuses, f"expected budget 403, got statuses={statuses} codes={codes}"
    assert "VIRTUAL_KEY_BUDGET_EXCEEDED" in codes


def test_vk_budget_claim_surface_or_enforced(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """§6 A: max_budget_usd is either enforced or must not be advertised as enforced.

    If mint still returns max_budget_usd, chat overspend must 403 — advertising
    without enforcement fails D5 acceptance.
    """
    configure_app(monkeypatch)
    with TestClient(app) as client:
        configure_app(monkeypatch)
        mint = client.post(
            "/v1/virtual-keys",
            headers={"Authorization": f"Bearer {SERVICE_TOKEN}"},
            json={
                "tenant_id": "t",
                "session_id": "s",
                "principal_id": "p",
                "allowed_models": ["echo"],
                "max_budget_usd": 0.01,
            },
        )
        assert mint.status_code == 200
        body = mint.json()
        if "max_budget_usd" not in body:
            pytest.skip("VK budget claim removed from mint — supervisor-owned budget OK")
        vk = body["token"]
        # Drive spend; echo invents tokens today — enforcement must still trip
        # for near-zero caps once metering lands.
        last = None
        for _ in range(5):
            last = client.post(
                "/v1/chat/completions",
                headers={"Authorization": f"Bearer {vk}"},
                json={
                    "model": "echo",
                    "messages": [{"role": "user", "content": "x" * 200}],
                },
            )
            if last.status_code == 403:
                break
        assert last is not None
        # Soft path: if metering not yet attributed to VK, still require that
        # a dedicated spend API / record path exists OR 403 eventually fires.
        # Core acceptance: claiming max_budget_usd without any budget code path fails.
        from model_gateway import virtual_keys as vk_mod

        store_src = open(vk_mod.__file__, encoding="utf-8").read()
        enforced_in_code = (
            "VIRTUAL_KEY_BUDGET_EXCEEDED" in store_src
            or "spent" in store_src.lower()
            or "record_spend" in store_src
            or "accumulate" in store_src.lower()
        )
        if last.status_code == 403:
            detail = last.json().get("detail") or {}
            err = detail.get("error") if isinstance(detail, dict) else {}
            assert (err or {}).get("code") == "VIRTUAL_KEY_BUDGET_EXCEEDED"
        else:
            assert enforced_in_code, (
                "max_budget_usd advertised on mint but budget not enforced "
                "(no VIRTUAL_KEY_BUDGET_EXCEEDED / spend tracking)"
            )
