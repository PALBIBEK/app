"""Gateway-backed summarizer for compaction."""

from __future__ import annotations

from typing import Any

from supervisor.seams.compaction.core import Summarizer, build_summary_request
from supervisor.seams.interfaces.model_gateway import ModelGatewayClient
from supervisor.seams.core.types import ChatMessage


def build_gateway_summarizer(
    gateway: ModelGatewayClient,
    *,
    model: str | None = None,
) -> Summarizer:
    """Wire a ``Summarizer`` to ``ModelGatewayClient`` (no tools offered)."""

    def summarize(
        messages: list[dict[str, Any]],
        previous_summary: str | None = None,
    ) -> str:
        request = build_summary_request(messages, previous_summary)
        chat = [
            ChatMessage(role=str(m.get("role") or "user"), content=_as_text(m.get("content")))
            for m in request
        ]
        completion = gateway.complete(chat, tools=[], model=model)
        return completion.message.content or ""

    return summarize


def _as_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if content is None:
        return ""
    return str(content)
