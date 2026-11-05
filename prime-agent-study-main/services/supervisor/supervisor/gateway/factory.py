"""Sole name→impl map for gateway providers (P-3 local to this service)."""

from __future__ import annotations

import httpx

from supervisor.gateway.config import Settings
from supervisor.gateway.providers.anthropic import AnthropicProvider
from supervisor.gateway.providers.base import Provider, ProviderRegistry
from supervisor.gateway.providers.echo import EchoChatProvider
from supervisor.gateway.providers.http import BaseUrl
from supervisor.gateway.providers.openai_compat import OpenAICompatProvider
from supervisor.gateway.providers.openrouter import OpenRouterChatProvider
from supervisor.gateway.vendor_keys import VendorKeys
from supervisor.gateway.vendors import VENDORS, Vendor


def vendor_provider(
    vendor: Vendor,
    settings: Settings,
    keys: VendorKeys | None = None,
    *,
    transport: httpx.BaseTransport | None = None,
) -> Provider:
    """The provider that calls ``vendor`` with the gateway's settings for it; its key (and a
    local server's address) is read from ``keys`` on every call (else it is the environment's)."""
    key = keys.source(vendor.id) if keys is not None else settings.vendor_key(vendor)
    fixed = settings.vendor_base_url(vendor)
    if vendor.api == "anthropic":
        return AnthropicProvider(api_key=key, base_url=fixed, transport=transport)
    if vendor.id == "openrouter":  # attribution headers on top of the OpenAI wire
        return OpenRouterChatProvider(api_key=key, base_url=fixed, transport=transport)
    base_url: BaseUrl = fixed
    if keys is not None and vendor.address_settable:  # a local server's: read on every call
        base_url = keys.address_source(vendor, fixed)
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


def build_provider_registry(settings: Settings, keys: VendorKeys | None = None) -> ProviderRegistry:
    registry = ProviderRegistry()
    registry.register(EchoChatProvider())
    for vendor in VENDORS.values():
        registry.register(vendor_provider(vendor, settings, keys))
    return registry
