"""D5 §6 A / T4 — /v1/readyz credentials gate for non-echo vs echo-only."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from supervisor.gateway.app import app
from supervisor.gateway.config import Settings
from supervisor.gateway.factory import build_provider_registry
from supervisor.gateway.virtual_keys import MemoryVirtualKeyStore

_ECHO_ONLY = {
    "echo": {"provider": "echo", "upstream_model": "echo"},
    "fake": {"provider": "echo", "upstream_model": "fake"},
}

_DEFAULT_WITH_GITHI = {
    "echo": {"provider": "echo", "upstream_model": "echo"},
    "githi": {
        "provider": "openrouter",
        "upstream_model": "anthropic/claude-sonnet-4",
    },
}


def _configure(
    monkeypatch: pytest.MonkeyPatch,
    *,
    routes: dict,
    openrouter_key: str = "",
    openai_key: str = "",
    token: str = "d5-readyz-token",
) -> None:
    monkeypatch.setenv("MODEL_GATEWAY_TOKEN", token)
    monkeypatch.setenv("ENV", "development")
    monkeypatch.delenv("AUTH_REQUIRED", raising=False)
    monkeypatch.setenv("MODEL_GATEWAY_ROUTES_JSON", json.dumps(routes))
    monkeypatch.setenv("OPENROUTER_API_KEY", openrouter_key)
    monkeypatch.setenv("OPENAI_API_KEY", openai_key)
    settings = Settings()
    app.state.settings = settings
    app.state.registry = build_provider_registry(settings)
    app.state.virtual_keys = MemoryVirtualKeyStore()


def test_d5_readyz_503_when_non_echo_default_lacks_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Focus #2: non-echo (githi) without OpenRouter key → /v1/readyz 503."""
    _configure(monkeypatch, routes=_DEFAULT_WITH_GITHI, openrouter_key="", openai_key="")
    with TestClient(app) as client:
        _configure(
            monkeypatch, routes=_DEFAULT_WITH_GITHI, openrouter_key="", openai_key=""
        )
        ready = client.get("/v1/readyz")
        health = client.get("/v1/healthz")
    assert health.status_code == 200
    assert ready.status_code == 503, ready.text
    body = ready.json()
    assert body.get("status") in ("not_ready", "error", "unavailable")
    assert body.get("reason") in ("credentials", "auth", "provider", "routes")


def test_d5_readyz_200_for_echo_only_without_vendor_keys(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Focus #2: echo-only route table is ready without vendor API keys."""
    _configure(monkeypatch, routes=_ECHO_ONLY, openrouter_key="", openai_key="")
    with TestClient(app) as client:
        _configure(monkeypatch, routes=_ECHO_ONLY, openrouter_key="", openai_key="")
        ready = client.get("/v1/readyz")
    assert ready.status_code == 200, ready.text
    assert ready.json().get("status") == "ready"
