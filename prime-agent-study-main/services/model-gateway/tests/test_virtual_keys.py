"""WP-M5 model-gateway virtual key mint / verify."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from model_gateway.app import app
from model_gateway.virtual_keys import (
    MemoryVirtualKeyStore,
    MintRequest,
    verify_virtual_key,
)


def test_memory_store_mint_revoke_expiry() -> None:
    store = MemoryVirtualKeyStore()
    claims = store.mint(
        MintRequest(
            tenant_id="t1",
            session_id="s1",
            principal_id="p1",
            allowed_models=("echo",),
            ttl_s=60,
        )
    )
    assert claims.token.startswith("vk_")
    assert verify_virtual_key(store, claims.token, route_id="echo")
    store.revoke(claims.token)
    with pytest.raises(PermissionError):
        verify_virtual_key(store, claims.token, route_id="echo")


@pytest.fixture()
def authed_client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("MODEL_GATEWAY_TOKEN", "secret-token")
    monkeypatch.delenv("MODEL_GATEWAY_ROUTES_JSON", raising=False)
    with TestClient(app) as c:
        yield c


def test_mint_and_chat_with_virtual_key(authed_client: TestClient) -> None:
    mint = authed_client.post(
        "/v1/virtual-keys",
        headers={"Authorization": "Bearer secret-token"},
        json={
            "tenant_id": "t1",
            "session_id": "s1",
            "principal_id": "p1",
            "allowed_models": ["echo"],
            "ttl_s": 120,
        },
    )
    assert mint.status_code == 200, mint.text
    token = mint.json()["token"]
    assert token.startswith("vk_")

    denied = authed_client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {token}"},
        json={"model": "fake", "messages": [{"role": "user", "content": "x"}]},
    )
    assert denied.status_code == 403

    ok = authed_client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {token}"},
        json={"model": "echo", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert ok.status_code == 200
    assert "echo:hi" in ok.json()["choices"][0]["message"]["content"]


def test_mint_requires_service_token(authed_client: TestClient) -> None:
    r = authed_client.post(
        "/v1/virtual-keys",
        headers={"Authorization": "Bearer vk_not_service"},
        json={
            "tenant_id": "t1",
            "session_id": "s1",
            "principal_id": "p1",
        },
    )
    assert r.status_code == 401

def test_empty_allowlist_denies_chat(authed_client: TestClient) -> None:
    mint = authed_client.post(
        "/v1/virtual-keys",
        headers={"Authorization": "Bearer secret-token"},
        json={
            "tenant_id": "t1",
            "session_id": "s1",
            "principal_id": "p1",
            "allowed_models": [],
            "ttl_s": 60,
        },
    )
    assert mint.status_code == 200
    token = mint.json()["token"]
    denied = authed_client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {token}"},
        json={"model": "echo", "messages": [{"role": "user", "content": "x"}]},
    )
    assert denied.status_code == 403


def test_mint_caps_ttl_and_budget(authed_client: TestClient) -> None:
    mint = authed_client.post(
        "/v1/virtual-keys",
        headers={"Authorization": "Bearer secret-token"},
        json={
            "tenant_id": "t1",
            "session_id": "s1",
            "principal_id": "p1",
            "allowed_models": ["echo"],
            "ttl_s": 999_999,
            "max_budget_usd": 1_000_000,
        },
    )
    assert mint.status_code == 200
    body = mint.json()
    assert body["max_budget_usd"] == 1000.0
    # expires within ~1 day of now
    import time

    assert body["expires_at"] <= time.time() + 86_400 + 5


def test_revoked_virtual_key_denied(authed_client: TestClient) -> None:
    mint = authed_client.post(
        "/v1/virtual-keys",
        headers={"Authorization": "Bearer secret-token"},
        json={
            "tenant_id": "t1",
            "session_id": "s1",
            "principal_id": "p1",
            "allowed_models": ["echo"],
        },
    )
    token = mint.json()["token"]
    rev = authed_client.post(
        "/v1/virtual-keys/revoke",
        headers={"Authorization": "Bearer secret-token"},
        params={"token": token},
    )
    assert rev.status_code == 200
    denied = authed_client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {token}"},
        json={"model": "echo", "messages": [{"role": "user", "content": "x"}]},
    )
    assert denied.status_code == 403
