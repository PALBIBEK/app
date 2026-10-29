"""Gateway-backed summarizer for compaction."""

from __future__ import annotations

from typing import Any

from supervisor.seams.compaction.core import build_summary_request
from supervisor.seams.core.types import ChatMessage
from supervisor.seams.interfaces.model_gateway import ModelGatewayClient

__all__ = ["GatewaySummarizer", "build_gateway_summarizer"]


class GatewaySummarizer:
    """A ``Summarizer`` over the model gateway (no tools offered) that counts what its
    calls spend, so a compaction's cost is not lost: ``spent`` holds input and output
    tokens and cost, summed over every call (a big span is summarised in parts)."""

    def __init__(self, gateway: ModelGatewayClient, *, model: str | None = None) -> None:
        self._gateway = gateway
        self._model = model
        self.spent: dict[str, Any] = {"input_tokens": 0, "output_tokens": 0, "cost": 0.0}

    def __call__(self, messages: list[dict[str, Any]], previous_summary: str | None = None) -> str:
        request = build_summary_request(messages, previous_summary)
        chat = [
            ChatMessage(role=str(m.get("role") or "user"), content=_as_text(m.get("content")))
            for m in request
        ]
        completion = self._gateway.complete(chat, tools=[], model=self._model)
        usage = completion.usage or {}
        self.spent["input_tokens"] += int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0)
        self.spent["output_tokens"] += int(usage.get("completion_tokens") or usage.get("output_tokens") or 0)
        self.spent["cost"] += float(usage.get("cost_total") or usage.get("cost") or 0.0)
        return completion.message.content or ""


def build_gateway_summarizer(gateway: ModelGatewayClient, *, model: str | None = None) -> GatewaySummarizer:
    """Wire a ``Summarizer`` to ``ModelGatewayClient`` (no tools offered)."""
    return GatewaySummarizer(gateway, model=model)


def _as_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if content is None:
        return ""
    return str(content)
