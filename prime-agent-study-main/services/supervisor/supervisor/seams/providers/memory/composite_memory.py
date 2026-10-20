"""CompositeContextMemoryProvider — compaction base + optional recall overlay."""

from __future__ import annotations

from typing import Any, Literal

from supervisor.seams.interfaces.context_memory import (
    CompactionDecision,
    CompactionPreparation,
    ContextMemoryProvider,
    MemoryInvokeContext,
    RecallBlock,
)


class CompositeContextMemoryProvider:
    """Delegate compaction to ``compaction``; recall to ``recall`` when set."""

    def __init__(
        self,
        *,
        compaction: ContextMemoryProvider,
        recall: ContextMemoryProvider | None = None,
    ) -> None:
        self._compaction = compaction
        self._recall = recall

    def estimate_tokens(self, messages: list[dict[str, Any]]) -> int:
        return self._compaction.estimate_tokens(messages)

    def before_compact(
        self,
        ctx: MemoryInvokeContext,
        preparation: CompactionPreparation,
        *,
        reason: Literal["manual", "threshold", "overflow"],
    ) -> CompactionDecision:
        return self._compaction.before_compact(ctx, preparation, reason=reason)

    def recall_for_turn(
        self,
        ctx: MemoryInvokeContext,
        *,
        user_prompt: str,
    ) -> RecallBlock | None:
        if self._recall is not None:
            return self._recall.recall_for_turn(ctx, user_prompt=user_prompt)
        return self._compaction.recall_for_turn(ctx, user_prompt=user_prompt)

    def append_plug_state(
        self,
        ctx: MemoryInvokeContext,
        custom_type: str,
        data: dict[str, Any],
    ) -> str:
        return self._compaction.append_plug_state(ctx, custom_type, data)


from supervisor.seams.providers.memory.null_memory import NullContextMemoryProvider

assert isinstance(
    CompositeContextMemoryProvider(compaction=NullContextMemoryProvider()),
    ContextMemoryProvider,
)
