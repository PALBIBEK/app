"""D5 §6 A / T1 — auth fail-closed when AUTH_REQUIRED or ENV=staging|production.

Independent acceptance: empty MODEL_GATEWAY_TOKEN must not leave chat open.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from model_gateway.app import app
from model_gateway.config import Settings
from model_gateway.factory import build_provider_registry
from model_gateway.virtual_keys import MemoryVirtualKeyStore


def _rebind(monkeypatch: pytest.MonkeyPatch, **env: str) -> None:
    for key in (
        "MODEL_GATEWAY_TOKEN",
        "AUTH_REQUIRED",
        "ENV",
        "OPENROUTER_API_KEY",
        "OPENAI_API_KEY",
        "MODEL_GATEWAY_ROUTES_JSON",
    ):
        monkeypatch.delenv(key, raising=False)
    for k, v in env.items():
        if v is None:
            continue
        monkeypatch.setenv(k, v)
    settings = Settings()
    app.state.settings = settings
    app.state.registry = build_provider_registry(settings)
    app.state.virtual_keys = MemoryVirtualKeyStore()


def _assert_fail_closed(client: TestClient) -> None:
    health = client.get("/v1/healthz")
    assert health.status_code == 200
    chat = client.post(
        "/v1/chat/completions",
        json={"model": "echo", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert chat.status_code in (401, 503), (
        f"empty token must fail closed on chat, got {chat.status_code}: {chat.text}"
    )
    body = chat.json()
    detail = body.get("detail") or body
    err = detail.get("error") if isinstance(detail, dict) else None
    code = (err or {}).get("code") if isinstance(err, dict) else None
    assert code in ("AUTH_UNCONFIGURED", "AUTH_REQUIRED", "AUTH_INVALID"), body


@pytest.mark.parametrize("env_name", ["production", "staging", "PRODUCTION", "Staging"])
def test_d5_empty_token_fail_closed_when_env_staging_or_production(
    monkeypatch: pytest.MonkeyPatch, env_name: str
) -> None:
    """Focus #1: ENV=staging|production + empty MODEL_GATEWAY_TOKEN → fail closed."""
    _rebind(monkeypatch, ENV=env_name, MODEL_GATEWAY_TOKEN="")
    with TestClient(app) as client:
        _rebind(monkeypatch, ENV=env_name, MODEL_GATEWAY_TOKEN="")
        _assert_fail_closed(client)


def test_d5_empty_token_fail_closed_when_auth_required(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Focus #1: AUTH_REQUIRED=true + empty MODEL_GATEWAY_TOKEN → fail closed."""
    _rebind(
        monkeypatch,
        ENV="development",
        AUTH_REQUIRED="true",
        MODEL_GATEWAY_TOKEN="",
    )
    with TestClient(app) as client:
        _rebind(
            monkeypatch,
            ENV="development",
            AUTH_REQUIRED="true",
            MODEL_GATEWAY_TOKEN="",
        )
        _assert_fail_closed(client)


def test_d5_dev_without_auth_required_may_remain_open_for_local_lab(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Dev lab without AUTH_REQUIRED may stay open — contrast to staging/prod."""
    _rebind(monkeypatch, ENV="development", MODEL_GATEWAY_TOKEN="")
    with TestClient(app) as client:
        _rebind(monkeypatch, ENV="development", MODEL_GATEWAY_TOKEN="")
        r = client.post(
            "/v1/chat/completions",
            json={"model": "echo", "messages": [{"role": "user", "content": "lab"}]},
        )
    # Not a D5 prod requirement — only assert we did not crash.
    assert r.status_code in (200, 401, 503)
