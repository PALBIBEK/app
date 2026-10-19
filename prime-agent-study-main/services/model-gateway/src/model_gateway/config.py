"""Settings for the independently deployable model gateway."""

from __future__ import annotations

import json
from typing import Any

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


# Route id → {provider, upstream_model, context_window}. ``model`` on chat requests
# is a route id. ``context_window`` is the upstream model's limit in tokens; the
# supervisor compacts a conversation against it.
DEFAULT_ROUTES: dict[str, dict[str, Any]] = {
    "echo": {"provider": "echo", "upstream_model": "echo", "context_window": 200_000},
    "fake": {"provider": "echo", "upstream_model": "fake", "context_window": 200_000},
    # Githi harness default route — OpenRouter Claude (keys only in gateway).
    "githi": {
        "provider": "openrouter",
        "upstream_model": "anthropic/claude-sonnet-4",
        "context_window": 200_000,
    },
}


def route_view(route_id: str, meta: dict[str, Any]) -> dict[str, Any]:
    """A route as consumers see it: what it calls and how much it can hold (no secrets)."""
    window = meta.get("context_window")
    if window is not None and (not isinstance(window, int) or isinstance(window, bool) or window <= 0):
        raise ValueError(f"route {route_id!r}: context_window must be a positive integer")
    return {
        "id": route_id,
        "provider": meta.get("provider"),
        "upstream_model": meta.get("upstream_model"),
        "context_window": window,
    }


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="", extra="ignore")

    model_gateway_token: str = Field(default="", alias="MODEL_GATEWAY_TOKEN")
    auth_required: bool = Field(default=False, alias="AUTH_REQUIRED")
    environment: str = Field(default="", alias="ENV")
    model_gateway_default_route: str = Field(
        default="echo", alias="MODEL_GATEWAY_DEFAULT_ROUTE"
    )
    model_gateway_routes_json: str = Field(
        default="", alias="MODEL_GATEWAY_ROUTES_JSON"
    )
    openai_api_key: str = Field(default="", alias="OPENAI_API_KEY")
    openai_base_url: str = Field(
        default="https://api.openai.com/v1", alias="OPENAI_BASE_URL"
    )
    openrouter_api_key: str = Field(default="", alias="OPENROUTER_API_KEY")
    openrouter_base_url: str = Field(
        default="https://openrouter.ai/api/v1", alias="OPENROUTER_BASE_URL"
    )
    port: int = Field(default=8090, alias="PORT")

    def auth_must_be_configured(self) -> bool:
        """Fail closed in production/staging or when AUTH_REQUIRED is set."""
        if self.auth_required:
            return True
        env = (self.environment or "").strip().lower()
        return env in ("production", "staging")

    def routes(self) -> dict[str, dict[str, Any]]:
        raw = self.model_gateway_routes_json.strip()
        if not raw:
            return dict(DEFAULT_ROUTES)
        parsed = json.loads(raw)
        if not isinstance(parsed, dict) or not parsed:
            raise ValueError("MODEL_GATEWAY_ROUTES_JSON must be a non-empty object")
        return parsed
