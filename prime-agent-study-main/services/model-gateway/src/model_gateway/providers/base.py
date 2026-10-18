"""Upstream chat provider Protocol + registry (gateway-local; not supervisor)."""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class ChatProvider(Protocol):
    name: str

    def complete(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        upstream_model: str,
        stream: bool,
    ) -> dict[str, Any]:
        """Return an OpenAI-compat chat.completion object (or stream payload)."""
        ...


class ProviderRegistry:
    def __init__(self) -> None:
        self._by_name: dict[str, ChatProvider] = {}

    def register(self, provider: ChatProvider) -> None:
        if provider.name in self._by_name:
            raise ValueError(f"duplicate provider: {provider.name}")
        self._by_name[provider.name] = provider

    def get(self, name: str) -> ChatProvider:
        try:
            return self._by_name[name]
        except KeyError as exc:
            raise KeyError(f"provider not registered: {name}") from exc
