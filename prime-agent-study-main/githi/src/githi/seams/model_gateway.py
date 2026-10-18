"""ModelGatewayClient seam definition (OpenAI-compat shaped)."""

from __future__ import annotations

from typing import Any, Iterator, Protocol, runtime_checkable

from ..llm.client import Completion
from ..llm.streaming import StreamEvent
from ..types import Message, ThinkingLevel


@runtime_checkable
class ModelGatewayClient(Protocol):
    """Definition: routed LLM access without embedding vendor SDK authority.

    First provider wraps `LLMClient`. Vendor keys may still live in-process
    this slice (removal = model-gateway QUEUE step).
    """

    def complete(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]],
        *,
        thinking_level: ThinkingLevel | None = None,
        model: str | None = None,
    ) -> Completion:
        ...

    def stream(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]],
        interrupt: Any = None,
        *,
        thinking_level: ThinkingLevel | None = None,
        model: str | None = None,
    ) -> Iterator[StreamEvent]:
        ...

    def close(self) -> None:
        ...
