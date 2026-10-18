"""Shared helpers for D5 gateway acceptance (not product fixtures)."""

from __future__ import annotations

import json
from typing import Any

import pytest

from model_gateway.app import app
from model_gateway.config import Settings
from model_gateway.factory import build_provider_registry
from model_gateway.virtual_keys import MemoryVirtualKeyStore

SERVICE_TOKEN = "d5-acceptance-service-token"

DEFAULT_ROUTES: dict[str, dict[str, Any]] = {
    "echo": {"provider": "echo", "upstream_model": "echo"},
    "fake": {"provider": "echo", "upstream_model": "fake"},
    "githi": {
        "provider": "openrouter",
        "upstream_model": "anthropic/claude-sonnet-4",
    },
    "openai-demo": {
        "provider": "openai",
        "upstream_model": "gpt-4o-mini",
    },
}


def configure_app(
    monkeypatch: pytest.MonkeyPatch,
    *,
    token: str = SERVICE_TOKEN,
    env: str = "development",
    auth_required: bool | None = None,
    routes: dict[str, dict[str, Any]] | None = None,
    openrouter_key: str = "",
    openai_key: str = "",
) -> Settings:
    """Set env + rebuild app.state so each test owns gateway settings."""
    monkeypatch.setenv("MODEL_GATEWAY_TOKEN", token)
    monkeypatch.setenv("ENV", env)
    if auth_required is None:
        monkeypatch.delenv("AUTH_REQUIRED", raising=False)
    else:
        monkeypatch.setenv("AUTH_REQUIRED", "true" if auth_required else "false")
    monkeypatch.setenv("OPENROUTER_API_KEY", openrouter_key)
    monkeypatch.setenv("OPENAI_API_KEY", openai_key)
    monkeypatch.setenv("MODEL_GATEWAY_DEFAULT_ROUTE", "echo")
    if routes is None:
        routes = dict(DEFAULT_ROUTES)
    monkeypatch.setenv("MODEL_GATEWAY_ROUTES_JSON", json.dumps(routes))
    settings = Settings()
    app.state.settings = settings
    app.state.registry = build_provider_registry(settings)
    app.state.virtual_keys = MemoryVirtualKeyStore()
    return settings
