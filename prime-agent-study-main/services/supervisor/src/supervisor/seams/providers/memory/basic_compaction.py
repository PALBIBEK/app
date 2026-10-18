"""BasicCompactionProvider — gateway summarizer + orphan-safe compaction helpers."""

from __future__ import annotations

from typing import Any, Literal

from supervisor.seams.compaction import (
    COMPACTION_NOTICE,
    build_gateway_summarizer,
    estimate_tokens as compaction_estimate_tokens,
    summarize_span,
)
from supervisor.seams.interfaces.context_memory import (
    CompactionDecision,
    CompactionPreparation,
    CompactionResult,
    ContextMemoryProvider,
    MemoryInvokeContext,
    RecallBlock,
)
from supervisor.seams.interfaces.model_gateway import ModelGatewayClient
from supervisor.seams.providers.memory.null_memory import NullContextMemoryProvider


class BasicCompactionProvider:
    """Compaction provider — summarizes dropped span via ``ModelGatewayClient``."""

    def __init__(
        self,
        gateway: ModelGatewayClient,
        *,
        summarizer_model: str | None = None,
    ) -> None:
        self._gateway = gateway
        self._summarizer_model = summarizer_model
        self._null = NullContextMemoryProvider()

    def estimate_tokens(self, messages: list[dict[str, Any]]) -> int:
        return compaction_estimate_tokens(messages)

    def before_compact(
        self,
        ctx: MemoryInvokeContext,
        preparation: CompactionPreparation,
        *,
        reason: Literal["manual", "threshold", "overflow"],
    ) -> CompactionDecision:
        _ = ctx, reason
        if not preparation.messages_to_summarize:
            return CompactionDecision(action="cancel")

        summarizer = build_gateway_summarizer(
            self._gateway,
            model=self._summarizer_model,
        )
        summary = summarize_span(
            preparation.messages_to_summarize,
            previous_summary=preparation.previous_summary,
            summarizer=summarizer,
        )
        if summary == COMPACTION_NOTICE and preparation.previous_summary is None:
            # Gateway failed or returned empty — let harness built-in try.
            return CompactionDecision(action="proceed_default")

        summary_message = {"role": "user", "content": summary}
        tokens_after = self.estimate_tokens([summary_message])
        return CompactionDecision(
            action="custom",
            result=CompactionResult(
                summary=summary,
                first_kept_entry_id=preparation.first_kept_entry_id,
                tokens_before=preparation.tokens_before,
                tokens_after=tokens_after,
            ),
        )

    def recall_for_turn(
        self,
        ctx: MemoryInvokeContext,
        *,
        user_prompt: str,
    ) -> RecallBlock | None:
        return self._null.recall_for_turn(ctx, user_prompt=user_prompt)

    def append_plug_state(
        self,
        ctx: MemoryInvokeContext,
        custom_type: str,
        data: dict[str, Any],
    ) -> str:
        return self._null.append_plug_state(ctx, custom_type, data)


from supervisor.seams.providers.gateway.fake_gateway import FakeModelGatewayClient

assert isinstance(
    BasicCompactionProvider(FakeModelGatewayClient()),
    ContextMemoryProvider,
)
