"""MarkdownRecallProvider — markdown bullet semantic recall."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from supervisor.seams.interfaces.context_memory import (
    CompactionDecision,
    CompactionPreparation,
    ContextMemoryProvider,
    MemoryInvokeContext,
    RecallBlock,
)
from supervisor.seams.providers.memory.null_memory import NullContextMemoryProvider
from supervisor.seams.recall.markdown_bullets import (
    DEFAULT_MIN_SCORE,
    recall_block_from_bullets,
    recall_markdown_bullets,
)


class MarkdownRecallProvider:
    """Semantic recall from ``MEMORY.md`` bullets — never raises."""

    def __init__(
        self,
        *,
        memory_root: Path | str | None = None,
        min_score: float = DEFAULT_MIN_SCORE,
    ) -> None:
        self._memory_root = Path(memory_root) if memory_root is not None else None
        self._min_score = min_score
        self._null = NullContextMemoryProvider()

    def _memory_path(self, ctx: MemoryInvokeContext) -> Path | None:
        if self._memory_root is not None:
            root = self._memory_root
            if root.is_file():
                return root
            return root / "MEMORY.md"
        if ctx.workspace_path:
            base = Path(ctx.workspace_path)
            for candidate in (base / "memory" / "MEMORY.md", base / "MEMORY.md"):
                if candidate.is_file():
                    return candidate
        return None

    def estimate_tokens(self, messages: list[dict[str, Any]]) -> int:
        return self._null.estimate_tokens(messages)

    def before_compact(
        self,
        ctx: MemoryInvokeContext,
        preparation: CompactionPreparation,
        *,
        reason: Literal["manual", "threshold", "overflow"],
    ) -> CompactionDecision:
        return self._null.before_compact(ctx, preparation, reason=reason)

    def recall_for_turn(
        self,
        ctx: MemoryInvokeContext,
        *,
        user_prompt: str,
    ) -> RecallBlock | None:
        path = self._memory_path(ctx)
        if path is None:
            return None
        try:
            hits = recall_markdown_bullets(
                path,
                user_prompt,
                min_score=self._min_score,
            )
            block = recall_block_from_bullets(hits)
        except Exception:  # noqa: BLE001 — degrade never fail run
            return None
        if not block:
            return None
        return RecallBlock(text=block, source="semantic")

    def append_plug_state(
        self,
        ctx: MemoryInvokeContext,
        custom_type: str,
        data: dict[str, Any],
    ) -> str:
        return self._null.append_plug_state(ctx, custom_type, data)


assert isinstance(MarkdownRecallProvider(), ContextMemoryProvider)
