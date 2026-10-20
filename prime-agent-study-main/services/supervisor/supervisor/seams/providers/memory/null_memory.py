"""NullContextMemoryProvider — default no-op session memory."""

from __future__ import annotations

import json
import uuid
from typing import Any, Literal

from supervisor.seams.interfaces.context_memory import (
    CompactionDecision,
    CompactionPreparation,
    ContextMemoryProvider,
    MemoryInvokeContext,
    RecallBlock,
)


def _estimate_chars_div35(messages: list[dict[str, Any]]) -> int:
    """Pi-style cheap token estimate (chars / 3.5)."""
    blob = json.dumps(messages, ensure_ascii=False, separators=(",", ":"))
    return max(0, int(len(blob) / 3.5))


class NullContextMemoryProvider:
    """Default provider — proceed_default compaction; no recall."""

    def __init__(self, *, rpc_configured: bool = False) -> None:
        self._rpc_configured = rpc_configured

    def estimate_tokens(self, messages: list[dict[str, Any]]) -> int:
        return _estimate_chars_div35(messages)

    def before_compact(
        self,
        ctx: MemoryInvokeContext,
        preparation: CompactionPreparation,
        *,
        reason: Literal["manual", "threshold", "overflow"],
    ) -> CompactionDecision:
        _ = ctx, preparation, reason
        return CompactionDecision(action="proceed_default")

    def recall_for_turn(
        self,
        ctx: MemoryInvokeContext,
        *,
        user_prompt: str,
    ) -> RecallBlock | None:
        _ = ctx, user_prompt
        return None

    def append_plug_state(
        self,
        ctx: MemoryInvokeContext,
        custom_type: str,
        data: dict[str, Any],
    ) -> str:
        _ = ctx, custom_type, data
        return f"plug-{uuid.uuid4().hex[:12]}"


# Satisfy structural typing for tests / isinstance checks.
assert isinstance(NullContextMemoryProvider(), ContextMemoryProvider)
