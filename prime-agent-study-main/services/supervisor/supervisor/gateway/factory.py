"""Sole name→impl map for gateway providers (P-3 local to this service)."""

from __future__ import annotations

import httpx

from supervisor.gateway.config import Settings
from supervisor.gateway.providers.anthropic import AnthropicProvider
from supervisor.gateway.providers.base import Provider, ProviderRegistry
from supervisor.gateway.providers.echo import EchoChatProvider
from supervisor.gateway.providers.openai_compat import OpenAICompatProvider
from supervisor.gateway.providers.openrouter import OpenRouterChatProvider
from supervisor.gateway.vendors import VENDORS, Vendor


def vendor_provider(
    vendor: Vendor, settings: Settings, *, transport: httpx.BaseTransport | None = None
) -> Provider:
    """The provider that calls ``vendor`` with the gateway's settings for it."""
    key, base_url = settings.vendor_key(vendor), settings.vendor_base_url(vendor)
    if vendor.api == "anthropic":
        return AnthropicProvider(api_key=key, base_url=base_url, transport=transport)
    if vendor.id == "openrouter":  # attribution headers on top of the OpenAI wire
        return OpenRouterChatProvider(api_key=key, base_url=base_url, transport=transport)
    return OpenAICompatProvider(
        transport=transport,
        name=vendor.id,
        api_key=key,
        base_url=base_url,
        responses=vendor.responses,
        key_required=vendor.key_required,
        key_env_name=vendor.key_env,
        base_url_env_name=vendor.base_url_env,
    )


def build_provider_registry(settings: Settings) -> ProviderRegistry:
    registry = ProviderRegistry()
    registry.register(EchoChatProvider())
    for vendor in VENDORS.values():
        registry.register(vendor_provider(vendor, settings))
    return registry
