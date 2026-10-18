"""LLMClient adapter → ModelGatewayClient."""

from __future__ import annotations

from typing import Any, Iterator

from ...llm.client import Completion, LLMClient
from ...llm.streaming import StreamEvent
from ...types import Message, ThinkingLevel


class LLMGatewayAdapter:
    """Provider: thin ModelGatewayClient over LLMClient (no new vendor SDK)."""

    def __init__(self, client: LLMClient) -> None:
        self._client = client

    def complete(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]],
        *,
        thinking_level: ThinkingLevel | None = None,
        model: str | None = None,
    ) -> Completion:
        return self._client.complete(
            messages,
            tools,
            thinking_level=thinking_level,
            model=model,
        )

    def stream(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]],
        interrupt: Any = None,
        *,
        thinking_level: ThinkingLevel | None = None,
        model: str | None = None,
    ) -> Iterator[StreamEvent]:
        return self._client.stream(
            messages,
            tools,
            interrupt,
            thinking_level=thinking_level,
            model=model,
        )

    def close(self) -> None:
        self._client.close()
