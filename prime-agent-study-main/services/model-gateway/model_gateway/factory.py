"""Sole name→impl map for gateway providers (P-3 local to this service)."""

from __future__ import annotations

from model_gateway.config import Settings
from model_gateway.providers.anthropic import AnthropicProvider
from model_gateway.providers.base import ProviderRegistry
from model_gateway.providers.echo import EchoChatProvider
from model_gateway.providers.openai_compat import OpenAICompatProvider
from model_gateway.providers.openrouter import OpenRouterChatProvider


def build_provider_registry(settings: Settings) -> ProviderRegistry:
    registry = ProviderRegistry()
    registry.register(EchoChatProvider())
    registry.register(
        OpenAICompatProvider(api_key=settings.openai_api_key, base_url=settings.openai_base_url)
    )
    registry.register(
        OpenRouterChatProvider(
            api_key=settings.openrouter_api_key, base_url=settings.openrouter_base_url
        )
    )
    registry.register(
        AnthropicProvider(api_key=settings.anthropic_api_key, base_url=settings.anthropic_base_url)
    )
    return registry
