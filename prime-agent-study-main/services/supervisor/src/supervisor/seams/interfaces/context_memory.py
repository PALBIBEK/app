"""ContextMemoryProvider seam — compaction + optional semantic recall."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Protocol, runtime_checkable

__all__ = [
    "CompactionDecision",
    "CompactionPreparation",
    "CompactionResult",
    "ContextMemoryProvider",
    "MemoryInvokeContext",
    "RecallBlock",
]


@dataclass(frozen=True)
class MemoryInvokeContext:
    session_id: str
    tenant_id: str = ""
    trace_id: str = ""
    workspace_path: str = ""


@dataclass(frozen=True)
class CompactionPreparation:
    """Input span — Pi CompactionPreparation / DSH shadowed range."""

    first_kept_entry_id: str
    messages_to_summarize: list[dict[str, Any]]
    tokens_before: int
    previous_summary: str | None
    context_window: int
    reserve_tokens: int


@dataclass(frozen=True)
class CompactionResult:
    summary: str
    first_kept_entry_id: str
    tokens_before: int
    tokens_after: int | None = None
    details: dict[str, Any] | None = None


@dataclass(frozen=True)
class CompactionDecision:
    action: Literal["proceed_default", "cancel", "custom"]
    result: CompactionResult | None = None


@dataclass(frozen=True)
class RecallBlock:
    text: str
    source: str  # semantic | branch | custom
    score: float | None = None


@runtime_checkable
class ContextMemoryProvider(Protocol):
    """Session memory — compaction + optional recall injection."""

    def estimate_tokens(self, messages: list[dict[str, Any]]) -> int:
        """Cheap estimate for pressure checks (Pi chars/3.5, DSH tokenMeter)."""
        ...

    def before_compact(
        self,
        ctx: MemoryInvokeContext,
        preparation: CompactionPreparation,
        *,
        reason: Literal["manual", "threshold", "overflow"],
    ) -> CompactionDecision:
        """Return proceed_default, cancel, or custom CompactionResult."""
        ...

    def recall_for_turn(
        self,
        ctx: MemoryInvokeContext,
        *,
        user_prompt: str,
    ) -> RecallBlock | None:
        """Optional semantic injection before agent loop."""
        ...

    def append_plug_state(
        self,
        ctx: MemoryInvokeContext,
        custom_type: str,
        data: dict[str, Any],
    ) -> str:
        """Persist extension-private entry id (Pi CustomEntry)."""
        ...
