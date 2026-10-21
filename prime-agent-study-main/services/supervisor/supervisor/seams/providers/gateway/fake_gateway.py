"""Fake ModelGatewayClient — canned complete/stream, no vendor SDK."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from supervisor.seams.core.types import ChatMessage, Completion, StreamEvent


class FakeModelGatewayClient:
    """Provider: returns canned assistant text (unit tests / defaults)."""

    def __init__(
        self,
        *,
        assistant_text: str = "ok",
        model: str = "fake",
        context_window: int | None = None,
    ) -> None:
        self._text = assistant_text
        self._model = model
        self._window = context_window
        self._closed = False

    def context_window(self, model: str) -> int | None:
        return self._window

    def complete(
        self,
        messages: list[ChatMessage],
        tools: list[dict[str, Any]],
        *,
        model: str | None = None,
        tool_choice: str | None = None,
    ) -> Completion:
        del tool_choice  # canned text either way
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
