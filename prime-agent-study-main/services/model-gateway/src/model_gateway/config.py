"""Settings for the independently deployable model gateway."""

from __future__ import annotations

import json
from typing import Any

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


# Route id → {provider, upstream_model}. ``model`` on chat requests is a route id.
DEFAULT_ROUTES: dict[str, dict[str, Any]] = {
    "echo": {"provider": "echo", "upstream_model": "echo"},
    "fake": {"provider": "echo", "upstream_model": "fake"},
    # Githi harness default route — OpenRouter Claude (keys only in gateway).
    "githi": {
        "provider": "openrouter",
        "upstream_model": "anthropic/claude-sonnet-4",
    },
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
