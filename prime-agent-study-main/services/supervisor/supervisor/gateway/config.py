"""Settings for the independently deployable model gateway."""

from __future__ import annotations

import re
import secrets
from collections.abc import Collection
from functools import cached_property
from pathlib import Path

from pydantic import Field, ValidationInfo, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from supervisor.gateway.routes import DEFAULT_ROUTES, VENDOR_ROUTES, RouteTable
from supervisor.gateway.vendor_keys import VendorKeys, sealing_key
from supervisor.gateway.vendors import VENDORS, Vendor, parse_base_url
from supervisor.gateway.virtual_keys import derive_virtual_key_secret

_HEADER_TOKEN = re.compile(r"^[!#$%&'*+.^_`|~0-9A-Za-z-]+$")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="", extra="ignore")

    model_gateway_token: str = Field(default="", alias="MODEL_GATEWAY_TOKEN")
    model_gateway_virtual_key_secret: str = Field(
        default="", alias="MODEL_GATEWAY_VIRTUAL_KEY_SECRET"
    )
    """Signs session virtual keys (at least 32 characters); derived from the service token
    when unset. Every gateway process needs the same one, so keys outlive a restart."""
    model_gateway_api_key_header: str = Field(
        default="Authorization", alias="MODEL_GATEWAY_API_KEY_HEADER"
    )
    """Header a caller's key arrives in (``Authorization`` takes ``Bearer <key>``)."""
    auth_required: bool = Field(default=False, alias="AUTH_REQUIRED")
    environment: str = Field(default="", alias="ENV")
    model_gateway_default_route: str = Field(default="echo", alias="MODEL_GATEWAY_DEFAULT_ROUTE")
    model_gateway_routes_json: str = Field(default="", alias="MODEL_GATEWAY_ROUTES_JSON")
    openai_api_key: str = Field(default="", alias="OPENAI_API_KEY")
    openai_base_url: str = Field(default=VENDORS["openai"].base_url, alias="OPENAI_BASE_URL")
    openrouter_api_key: str = Field(default="", alias="OPENROUTER_API_KEY")
    openrouter_base_url: str = Field(
        default=VENDORS["openrouter"].base_url, alias="OPENROUTER_BASE_URL"
    )
    anthropic_api_key: str = Field(default="", alias="ANTHROPIC_API_KEY")
    anthropic_base_url: str = Field(
        default=VENDORS["anthropic"].base_url, alias="ANTHROPIC_BASE_URL"
    )
    deepseek_api_key: str = Field(default="", alias="DEEPSEEK_API_KEY")
    deepseek_base_url: str = Field(default=VENDORS["deepseek"].base_url, alias="DEEPSEEK_BASE_URL")
    zai_api_key: str = Field(default="", alias="ZAI_API_KEY")
    zai_base_url: str = Field(default=VENDORS["zai"].base_url, alias="ZAI_BASE_URL")
    dashscope_api_key: str = Field(default="", alias="DASHSCOPE_API_KEY")
    dashscope_base_url: str = Field(default=VENDORS["qwen"].base_url, alias="DASHSCOPE_BASE_URL")
    local_model_api_key: str = Field(default="", alias="LOCAL_MODEL_API_KEY")
    """Optional: a local server started with a key (vLLM ``--api-key``); Ollama needs none."""
    local_model_base_url: str = Field(default="", alias="LOCAL_MODEL_BASE_URL")
    """A local OpenAI-compatible server (``http://localhost:11434/v1`` for Ollama); unset, the
    ``local`` vendor is off."""
    model_gateway_keys_file: str = Field(default="", alias="MODEL_GATEWAY_KEYS_FILE")
    """Where keys set through the gateway's API are kept (sealed); unset, vendor keys come
    from the environment only."""
    model_gateway_keys_secret: str = Field(default="", alias="MODEL_GATEWAY_KEYS_SECRET")
    """Key material the key file is sealed with (at least 32 characters); the service token
    when unset. Every gateway process sharing the file needs the same one."""
    port: int = Field(default=8090, alias="PORT")

    @field_validator(
        "openai_base_url",
        "openrouter_base_url",
        "anthropic_base_url",
        "deepseek_base_url",
        "zai_base_url",
        "dashscope_base_url",
        "local_model_base_url",
    )
    @classmethod
    def _base_url(cls, value: str, info: ValidationInfo) -> str:
        field = cls.model_fields[info.field_name or ""]
        return parse_base_url(str(field.alias), value)

    @field_validator("model_gateway_keys_secret")
    @classmethod
    def _keys_secret(cls, value: str) -> str:
        if value and len(value) < 32:
            raise ValueError("MODEL_GATEWAY_KEYS_SECRET must be at least 32 characters")
        return value

    @field_validator("model_gateway_api_key_header")
    @classmethod
    def _header_token(cls, value: str) -> str:
        value = value.strip()
        if not _HEADER_TOKEN.fullmatch(value):
            raise ValueError("MODEL_GATEWAY_API_KEY_HEADER must be an HTTP header name")
        return value

    def virtual_key_secret(self) -> bytes:
        """The secret virtual keys are signed with: the configured one, else derived from the
        service token, else (no token: nothing can mint) one for this process alone."""
        if self.model_gateway_virtual_key_secret:
            return self.model_gateway_virtual_key_secret.encode()
        if self.model_gateway_token:
            return derive_virtual_key_secret(self.model_gateway_token)
        return secrets.token_bytes(32)

    def auth_must_be_configured(self) -> bool:
        """Fail closed in production/staging or when AUTH_REQUIRED is set."""
        if self.auth_required:
            return True
        return self.environment.strip().lower() in ("production", "staging")

    def _setting(self, env_name: str) -> str:
        """The value of the setting read from the environment variable ``env_name``."""
        name = next(n for n, f in type(self).model_fields.items() if f.alias == env_name)
        return str(getattr(self, name)).strip()

    def vendor_key(self, vendor: Vendor) -> str:
        return self._setting(vendor.key_env)

    def vendor_base_url(self, vendor: Vendor) -> str:
        return self._setting(vendor.base_url_env)

    def vendor_keys(self) -> dict[str, str]:
        """Each vendor's key in the environment (empty when unset)."""
        return {vendor.id: self.vendor_key(vendor) for vendor in VENDORS.values()}

    def vendor_key_store(self) -> VendorKeys:
        """The vendors' keys: the environment's, and the key file's when one is configured."""
        if not self.model_gateway_keys_file.strip():
            return VendorKeys(self.vendor_keys())
        material = self.model_gateway_keys_secret or self.model_gateway_token
        if not material:
            raise ValueError(
                "MODEL_GATEWAY_KEYS_FILE needs MODEL_GATEWAY_KEYS_SECRET or MODEL_GATEWAY_TOKEN"
            )
        return VendorKeys(
            self.vendor_keys(),
            path=Path(self.model_gateway_keys_file.strip()),
            secret=sealing_key(material),
        )

    @cached_property
    def route_table(self) -> RouteTable:
        """The configured routes (``MODEL_GATEWAY_ROUTES_JSON``, else the defaults).

        Raises :class:`~supervisor.gateway.routes.RouteConfigError` on an invalid table.
        """
        raw = self.model_gateway_routes_json.strip()
        return RouteTable.from_json(raw) if raw else RouteTable(DEFAULT_ROUTES)

    def routes(self, keyed: Collection[str] | None = None) -> RouteTable:
        """The configured routes plus the default routes of each vendor with a key: those in
        ``keyed``, else those with a key in the environment."""
        keyed = {v for v, key in self.vendor_keys().items() if key} if keyed is None else keyed
        table = self.route_table
        for vendor in VENDORS:  # the vendor table's order, so listings are stable
            if vendor in keyed and vendor in VENDOR_ROUTES:
                table = table.with_defaults(VENDOR_ROUTES[vendor])
        return table
