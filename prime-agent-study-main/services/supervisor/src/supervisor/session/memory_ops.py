"""Memory session ops — ContextMemoryProvider + compaction journal events."""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from typing import Literal

from supervisor.agent.events import CompactionEnd, CompactionStart, MemoryRecall
from supervisor.journal.writer import JournalWriter
from supervisor.seams import (
    CompactionPreparation,
    ContextMemoryProvider,
    MemoryInvokeContext,
    RecallBlock,
)


@dataclass(frozen=True)
class CompactMemoryOutcome:
    """Result of a journaled compaction RPC."""

    compaction_id: str
    stop_reason: str  # ok | error | cancelled | deferred
    summary: str | None = None
    tokens_before: int | None = None
    tokens_after: int | None = None
    summary_digest: str | None = None


@dataclass(frozen=True)
class RecallMemoryOutcome:
    """Result of a journaled semantic recall."""

    block_id: str
    injected: bool
    source: str
    text: str | None = None
    token_estimate: int = 0


class MemoryOps:
    """Bridge: ContextMemoryProvider ↔ compaction_start / compaction_end journal events."""

    def __init__(
        self,
        *,
        session_id: str,
        memory: ContextMemoryProvider,
        writer: JournalWriter,
        tenant_id: str = "",
        workspace_path: str = "",
    ) -> None:
        self._session_id = session_id
        self._memory = memory
        self._writer = writer
        self._tenant_id = tenant_id
        self._workspace_path = workspace_path

    def _compaction_id(self) -> str:
        return f"cmp-{uuid.uuid4().hex[:12]}"

    def _ctx(self) -> MemoryInvokeContext:
        return MemoryInvokeContext(
            session_id=self._session_id,
            tenant_id=self._tenant_id,
            workspace_path=self._workspace_path,
        )

    @staticmethod
    def _summary_digest(summary: str) -> str:
        return hashlib.sha256(summary.encode()).hexdigest()[:32]

    async def compact(
        self,
        preparation: CompactionPreparation,
        *,
        reason: Literal["manual", "threshold", "overflow"],
        turn_id: str | None = None,
        trace_id: str | None = None,
    ) -> CompactMemoryOutcome:
        compaction_id = self._compaction_id()
        await self._writer.append(
            self._session_id,
            CompactionStart(
                compaction_id=compaction_id,
                reason=reason,
                tokens_before=preparation.tokens_before,
                first_kept_entry_id=preparation.first_kept_entry_id,
            ),
            turn_id=turn_id,
            trace_id=trace_id,
        )
        try:
            decision = self._memory.before_compact(
                self._ctx(),
                preparation,
                reason=reason,
            )
        except Exception:
            await self._writer.append(
                self._session_id,
                CompactionEnd(
                    compaction_id=compaction_id,
                    stop_reason="error",
                ),
                turn_id=turn_id,
                trace_id=trace_id,
            )
            raise

        if decision.action == "cancel":
            await self._writer.append(
                self._session_id,
                CompactionEnd(
                    compaction_id=compaction_id,
                    stop_reason="cancelled",
                ),
                turn_id=turn_id,
                trace_id=trace_id,
            )
            return CompactMemoryOutcome(
                compaction_id=compaction_id,
                stop_reason="cancelled",
                tokens_before=preparation.tokens_before,
            )

        if decision.action == "proceed_default":
            await self._writer.append(
                self._session_id,
                CompactionEnd(
                    compaction_id=compaction_id,
                    stop_reason="deferred",
                ),
                turn_id=turn_id,
                trace_id=trace_id,
            )
            return CompactMemoryOutcome(
                compaction_id=compaction_id,
                stop_reason="deferred",
                tokens_before=preparation.tokens_before,
            )

        result = decision.result
        if result is None:
            await self._writer.append(
                self._session_id,
                CompactionEnd(
                    compaction_id=compaction_id,
                    stop_reason="error",
                ),
                turn_id=turn_id,
                trace_id=trace_id,
            )
            return CompactMemoryOutcome(
                compaction_id=compaction_id,
                stop_reason="error",
                tokens_before=preparation.tokens_before,
            )

        digest = self._summary_digest(result.summary)
        await self._writer.append(
            self._session_id,
            CompactionEnd(
                compaction_id=compaction_id,
                stop_reason="ok",
                tokens_after=result.tokens_after,
                summary_digest=digest,
            ),
            turn_id=turn_id,
            trace_id=trace_id,
        )
        return CompactMemoryOutcome(
            compaction_id=compaction_id,
            stop_reason="ok",
            summary=result.summary,
            tokens_before=result.tokens_before,
            tokens_after=result.tokens_after,
            summary_digest=digest,
        )

    async def recall(
        self,
        user_prompt: str,
        *,
        turn_id: str | None = None,
        trace_id: str | None = None,
    ) -> RecallMemoryOutcome:
        block_id = f"rec-{uuid.uuid4().hex[:12]}"
        block: RecallBlock | None = None
        try:
            block = self._memory.recall_for_turn(
                self._ctx(),
                user_prompt=user_prompt,
            )
        except Exception:
            block = None

        if block is None or not (block.text or "").strip():
            await self._writer.append(
                self._session_id,
                MemoryRecall(
                    block_id=block_id,
                    source="semantic",
                    token_estimate=0,
                    injected=False,
                ),
                turn_id=turn_id,
                trace_id=trace_id,
            )
            return RecallMemoryOutcome(
                block_id=block_id,
                injected=False,
                source="semantic",
            )

        token_estimate = self._memory.estimate_tokens(
            [{"role": "user", "content": block.text}],
        )
        await self._writer.append(
            self._session_id,
            MemoryRecall(
                block_id=block_id,
                source=block.source,
                token_estimate=token_estimate,
                injected=True,
            ),
            turn_id=turn_id,
            trace_id=trace_id,
        )
        return RecallMemoryOutcome(
            block_id=block_id,
            injected=True,
            source=block.source,
            text=block.text,
            token_estimate=token_estimate,
        )
