"""ModelGatewayClient seam definition (OpenAI-compat shaped)."""

from __future__ import annotations

from typing import Any, Iterator, Protocol, runtime_checkable

from supervisor.seams.core.types import ChatMessage, Completion, StreamEvent

__all__ = ["ChatMessage", "Completion", "ModelGatewayClient", "StreamEvent"]


@runtime_checkable
class ModelGatewayClient(Protocol):
    """Definition: routed LLM access without embedding vendor SDK authority.

    OpenAI-compat vocabulary (G-1). HTTP deployable:
    ``services/model-gateway/`` + ``HttpModelGatewayClient``.
    """

    def complete(
        self,
        messages: list[ChatMessage],
        tools: list[dict[str, Any]],
        *,
        model: str | None = None,
    ) -> Completion:
        ...

    def stream(
        self,
        messages: list[ChatMessage],
        tools: list[dict[str, Any]],
        *,
        model: str | None = None,
    ) -> Iterator[StreamEvent]:
        ...

    def close(self) -> None:
        ...
