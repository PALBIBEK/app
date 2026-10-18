"""Fake ModelGatewayClient — canned complete/stream, no vendor SDK."""

from __future__ import annotations

from typing import Any, Iterator

from supervisor.seams.core.types import ChatMessage, Completion, StreamEvent


class FakeModelGatewayClient:
    """Provider: returns canned assistant text (unit tests / defaults)."""

    def __init__(self, *, assistant_text: str = "ok", model: str = "fake") -> None:
        self._text = assistant_text
        self._model = model
        self._closed = False

    def complete(
        self,
        messages: list[ChatMessage],
        tools: list[dict[str, Any]],
        *,
        model: str | None = None,
    ) -> Completion:
        return Completion(
            message=ChatMessage(role="assistant", content=self._text),
            model=model or self._model,
            finish_reason="stop",
        )

    def stream(
        self,
        messages: list[ChatMessage],
        tools: list[dict[str, Any]],
        *,
        model: str | None = None,
    ) -> Iterator[StreamEvent]:
        m = model or self._model
        for ch in self._text:
            yield StreamEvent(type="delta", delta=ch, model=m)
        yield StreamEvent(type="done", delta="", finish_reason="stop", model=m)

    def close(self) -> None:
        self._closed = True
