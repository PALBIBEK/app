"""Sole name→impl map for gateway providers (P-3 local to this service)."""

from __future__ import annotations

from model_gateway.config import Settings
from model_gateway.providers.base import ProviderRegistry
from model_gateway.providers.echo import EchoChatProvider
from model_gateway.providers.openai_compat import OpenAICompatProvider


def build_provider_registry(settings: Settings) -> ProviderRegistry:
    registry = ProviderRegistry()
    registry.register(EchoChatProvider())
    registry.register(
        OpenAICompatProvider(
            api_key=settings.openai_api_key,
            base_url=settings.openai_base_url,
        )
    )
    return registry
