"""ModelGatewayClient seam definition (OpenAI-compat shaped)."""

from __future__ import annotations

from typing import Any, Iterator, Protocol, runtime_checkable

from supervisor.seams.core.types import ChatMessage, Completion, StreamEvent

__all__ = ["ChatMessage", "Completion", "ModelCatalog", "ModelGatewayClient", "StreamEvent"]


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


@runtime_checkable
class ModelCatalog(Protocol):
    """Optional: a gateway that can say how much a model holds.

    The model behind a route belongs to the gateway, so the gateway is the one
    place that knows its context window. Compaction sizes a conversation
    against it when the session's memory profile pins no window of its own.
    """

    def context_window(self, model: str) -> int | None:
        """Tokens ``model`` (a route id) accepts, or None when the gateway cannot say."""
        ...
