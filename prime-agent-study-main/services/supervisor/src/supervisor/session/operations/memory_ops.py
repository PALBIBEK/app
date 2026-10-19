"""Memory session operations — ContextMemoryProvider + compaction journal events.

Bridges ContextMemoryProvider with journal events for memory compaction
and recall tracking. All operations are journaled for crash recovery
and audit purposes.

Features:
- Memory compaction with journaled start/end events
- Semantic recall with injection tracking
- Summary digest computation for verification
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from supervisor.agent.events import CompactionEnd, CompactionStart, MemoryRecall
from supervisor.seams import (
    CompactionPreparation,
    MemoryInvokeContext,
    RecallBlock,
)

if TYPE_CHECKING:
    from supervisor.journal.writer import JournalWriter
    from supervisor.seams import ContextMemoryProvider

__all__ = [
    "MemoryOps",
    "CompactMemoryOutcome",
    "RecallMemoryOutcome",
]

logger = logging.getLogger(__name__)


# =============================================================================
# Outcome Types
# =============================================================================


@dataclass(frozen=True, slots=True)
class CompactMemoryOutcome:
    """Result of a journaled compaction operation.

    Attributes:
        compaction_id: Unique identifier for this compaction
        stop_reason: How compaction ended (ok, error, cancelled, deferred)
        summary: The generated summary text (if successful)
        tokens_before: Token count before compaction
        tokens_after: Token count after compaction
        summary_digest: SHA256 hash of summary for verification
    """

    compaction_id: str
    stop_reason: str
    summary: str | None = None
    tokens_before: int | None = None
    tokens_after: int | None = None
    summary_digest: str | None = None


@dataclass(frozen=True, slots=True)
class RecallMemoryOutcome:
    """Result of a journaled semantic recall.

    Attributes:
        block_id: Unique identifier for this recall
        injected: Whether content was injected into context
        source: Source of the recalled content
        text: The recalled text content
        token_estimate: Estimated token count
    """

    block_id: str
    injected: bool
    source: str
    text: str | None = None
    token_estimate: int = 0


# =============================================================================
# Memory Operations
# =============================================================================


class MemoryOps:
    """Bridge: ContextMemoryProvider ↔ compaction_start / compaction_end journal events.

    Provides journaled memory operations for compaction and recall,
    ensuring all memory state changes are tracked for crash recovery
    and audit purposes.

    Example:
        ops = MemoryOps(
            session_id="sess-123",
            memory=memory_provider,
            writer=writer,
            tenant_id="tenant-1",
        )

        outcome = await ops.compact(
            preparation=prep,
            reason="threshold",
        )

        recall = await ops.recall(user_prompt="...")
    """

    def __init__(
        self,
        *,
        session_id: str,
        memory: ContextMemoryProvider,
        writer: JournalWriter,
        tenant_id: str = "",
        workspace_path: str = "",
    ) -> None:
        """Initialize memory operations.

        Args:
            session_id: The session context
            memory: ContextMemoryProvider instance
            writer: Journal writer for event logging
            tenant_id: Tenant identifier
            workspace_path: Workspace path for context
        """
        self._session_id = session_id
        self._memory = memory
        self._writer = writer
        self._tenant_id = tenant_id
        self._workspace_path = workspace_path

    def _compaction_id(self) -> str:
        """Generate unique compaction identifier."""
        return f"cmp-{uuid.uuid4().hex[:12]}"

    def _ctx(self) -> MemoryInvokeContext:
        """Build memory invocation context."""
        return MemoryInvokeContext(
            session_id=self._session_id,
            tenant_id=self._tenant_id,
            workspace_path=self._workspace_path,
        )

    @staticmethod
    def _summary_digest(summary: str) -> str:
        """Compute SHA256 digest of summary for verification.

        Args:
            summary: The summary text

        Returns:
            First 32 characters of hex digest
        """
        return hashlib.sha256(summary.encode()).hexdigest()[:32]

    async def compact(
        self,
        preparation: CompactionPreparation,
        *,
        reason: Literal["manual", "threshold", "overflow"],
        turn_id: str | None = None,
        trace_id: str | None = None,
    ) -> CompactMemoryOutcome:
        """Perform journaled memory compaction.

        Journals CompactionStart, calls provider, journals CompactionEnd
        with outcome. Handles cancellation, deferral, and errors.

        Args:
            preparation: Compaction preparation data
            reason: Why compaction is being performed
            turn_id: Optional turn identifier
            trace_id: Optional trace identifier

        Returns:
            CompactMemoryOutcome with result details
        """
        compaction_id = self._compaction_id()

        # Journal start
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

        # Call provider
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

        # Handle cancellation
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

        # Handle deferral to default
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

        # Handle missing result
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

        # Success
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

        logger.debug(
            "memory compaction completed",
            extra={
                "session_id": self._session_id,
                "compaction_id": compaction_id,
                "tokens_before": result.tokens_before,
                "tokens_after": result.tokens_after,
            },
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
        """Perform journaled semantic recall.

        Calls provider for recall, journals MemoryRecall event
        with injection status.

        Args:
            user_prompt: The user's prompt for context
            turn_id: Optional turn identifier
            trace_id: Optional trace identifier

        Returns:
            RecallMemoryOutcome with recall details
        """
        block_id = f"rec-{uuid.uuid4().hex[:12]}"
        block: RecallBlock | None = None

        try:
            block = self._memory.recall_for_turn(
                self._ctx(),
                user_prompt=user_prompt,
            )
        except Exception:
            block = None

        # No content to inject
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

        # Estimate tokens and journal
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

        logger.debug(
            "memory recall completed",
            extra={
                "session_id": self._session_id,
                "block_id": block_id,
                "source": block.source,
                "token_estimate": token_estimate,
            },
        )

        return RecallMemoryOutcome(
            block_id=block_id,
            injected=True,
            source=block.source,
            text=block.text,
            token_estimate=token_estimate,
        )
