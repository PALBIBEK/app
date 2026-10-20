"""Settings for the independently deployable model gateway."""

from __future__ import annotations

import re
from functools import cached_property

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from supervisor.gateway.routes import DEFAULT_ROUTES, VENDOR_ROUTES, RouteTable

_HEADER_TOKEN = re.compile(r"^[!#$%&'*+.^_`|~0-9A-Za-z-]+$")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="", extra="ignore")

    model_gateway_token: str = Field(default="", alias="MODEL_GATEWAY_TOKEN")
    model_gateway_api_key_header: str = Field(
        default="Authorization", alias="MODEL_GATEWAY_API_KEY_HEADER"
    )
    """Header a caller's key arrives in (``Authorization`` takes ``Bearer <key>``)."""
    auth_required: bool = Field(default=False, alias="AUTH_REQUIRED")
    environment: str = Field(default="", alias="ENV")
    model_gateway_default_route: str = Field(default="echo", alias="MODEL_GATEWAY_DEFAULT_ROUTE")
    model_gateway_routes_json: str = Field(default="", alias="MODEL_GATEWAY_ROUTES_JSON")
    openai_api_key: str = Field(default="", alias="OPENAI_API_KEY")
    openai_base_url: str = Field(default="https://api.openai.com/v1", alias="OPENAI_BASE_URL")
    openrouter_api_key: str = Field(default="", alias="OPENROUTER_API_KEY")
    openrouter_base_url: str = Field(
        default="https://openrouter.ai/api/v1", alias="OPENROUTER_BASE_URL"
    )
    anthropic_api_key: str = Field(default="", alias="ANTHROPIC_API_KEY")
    anthropic_base_url: str = Field(default="https://api.anthropic.com", alias="ANTHROPIC_BASE_URL")
    port: int = Field(default=8090, alias="PORT")

    @field_validator("model_gateway_api_key_header")
    @classmethod
    def _header_token(cls, value: str) -> str:
        value = value.strip()
        if not _HEADER_TOKEN.fullmatch(value):
            raise ValueError("MODEL_GATEWAY_API_KEY_HEADER must be an HTTP header name")
        return value

    def auth_must_be_configured(self) -> bool:
        """Fail closed in production/staging or when AUTH_REQUIRED is set."""
        if self.auth_required:
            return True
        return self.environment.strip().lower() in ("production", "staging")

    def vendor_keys(self) -> dict[str, str]:
        return {
            "openai": self.openai_api_key.strip(),
            "openrouter": self.openrouter_api_key.strip(),
            "anthropic": self.anthropic_api_key.strip(),
        }

    @cached_property
    def route_table(self) -> RouteTable:
        """Configured routes plus the routes each set vendor key unlocks.

        Raises :class:`~supervisor.gateway.routes.RouteConfigError` on an invalid table.
        """
        raw = self.model_gateway_routes_json.strip()
        table = RouteTable.from_json(raw) if raw else RouteTable(DEFAULT_ROUTES)
        for vendor, key in self.vendor_keys().items():
            if key:
                table = table.with_defaults(VENDOR_ROUTES[vendor])
        return table

    def routes(self) -> RouteTable:
        return self.route_table
