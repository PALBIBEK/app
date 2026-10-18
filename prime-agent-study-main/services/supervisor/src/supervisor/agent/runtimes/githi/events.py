"""
Githi event emission system.

Event emission helpers following contracts/githi_events.md and AGENT_CONTRACT.md.
All events include timestamps and follow the protocol schema.

Pattern: TYPE_CHECKING for circular imports (PYTHON_PATTERNS_MASTERCLASS.md Section 2.1)
"""

from __future__ import annotations

import typing as t
import uuid
from datetime import datetime, timezone
from dataclasses import dataclass, asdict

if t.TYPE_CHECKING:
    from supervisor.agent.runtimes.githi.state import GithiAgentState, Usage


@dataclass(frozen=True)
class Event:
    """
    Base event structure.

    Immutable event following DTO pattern (PYTHON_PATTERNS_MASTERCLASS.md Section 7.1).
    All events have type, timestamp, and optional payload.
    """

    type: str
    timestamp: str
    payload: dict[str, t.Any] | None = None

    def to_dict(self) -> dict[str, t.Any]:
        """Serialize event to dictionary"""
        result = {
            "type": self.type,
            "timestamp": self.timestamp,
        }
        if self.payload:
            result.update(self.payload)
        return result


class GithiEventEmitter:
    """
    Event emission for Githi runtime.

    Emits all required events from AGENT_CONTRACT.md plus Githi extensions
    from contracts/githi_events.md.

    Event pairing rules:
    - tool_start/tool_end must pair (same tool_call_id)
    - compaction_start/compaction_end must pair (same compaction_id)
    - run_start/run_end must pair (same session_id)

    Pattern: Service class with immutable events (PYTHON_PATTERNS_MASTERCLASS.md)
    """

    def __init__(self, session_id: str) -> None:
        """
        Initialize event emitter for a session.

        Args:
            session_id: Session identifier for all events
        """
        self.session_id = session_id

    @staticmethod
    def _now() -> str:
        """Current UTC timestamp in ISO 8601 format"""
        return datetime.now(timezone.utc).isoformat()

    # === Required Events (AGENT_CONTRACT.md) ===

    def run_start(self, model: str, context_window: int) -> Event:
        """
        Emit run_start event.

        When: Session begins

        Args:
            model: Model identifier
            context_window: Context window size
        """
        return Event(
            type="run_start",
            timestamp=self._now(),
            payload={
                "session_id": self.session_id,
                "model": model,
                "context_window": context_window,
            },
        )

    def run_end(self, stop_reason: str, state: GithiAgentState) -> Event:
        """
        Emit run_end event.

        When: Session completes

        Args:
            stop_reason: "done" | "max_turns" | "error" | "stopped" | "interrupted"
            state: Final session state
        """
        return Event(
            type="run_end",
            timestamp=self._now(),
            payload={
                "session_id": self.session_id,
                "stop_reason": stop_reason,
                "turn_count": state.turn_count,
                "tool_call_count": state.tool_call_count,
                "total_tokens": state.total_usage.total_tokens,
                "total_cost_usd": str(state.total_cost_usd),
            },
        )

    def turn_start(self, turn_number: int) -> Event:
        """
        Emit turn_start event.

        When: Model round-trip begins

        Args:
            turn_number: Turn counter (1-indexed)
        """
        return Event(
            type="turn_start",
            timestamp=self._now(),
            payload={
                "session_id": self.session_id,
                "turn_number": turn_number,
            },
        )

    def turn_end(self, turn_number: int, usage: Usage, cost_usd: str) -> Event:
        """
        Emit turn_end event.

        When: Model round-trip completes

        Args:
            turn_number: Turn counter
            usage: Token usage for this turn
            cost_usd: Cost for this turn
        """
        return Event(
            type="turn_end",
            timestamp=self._now(),
            payload={
                "session_id": self.session_id,
                "turn_number": turn_number,
                "usage": usage.to_dict(),
                "cost_usd": cost_usd,
            },
        )

    def tool_start(
        self,
        tool_name: str,
        tool_input: dict[str, t.Any],
        *,
        tool_call_id: str | None = None,
    ) -> Event:
        """
        Emit tool_start event BEFORE tool execution.

        CRITICAL: This event MUST be emitted BEFORE tool runs for journal integrity.
        Unpaired tool_start indicates crash during execution.

        Args:
            tool_name: Tool being called
            tool_input: Tool arguments
            tool_call_id: Unique ID for pairing (auto-generated if not provided)
        """
        call_id = tool_call_id or str(uuid.uuid4())

        return Event(
            type="tool_start",
            timestamp=self._now(),
            payload={
                "session_id": self.session_id,
                "tool_call_id": call_id,
                "tool_name": tool_name,
                "tool_input": tool_input,
            },
        )

    def tool_end(
        self,
        tool_call_id: str,
        tool_output: t.Any,
        *,
        success: bool = True,
        error: str | None = None,
    ) -> Event:
        """
        Emit tool_end event after tool execution.

        Pairs with tool_start. Same tool_call_id required.

        Args:
            tool_call_id: Same ID from tool_start
            tool_output: Tool result (success) or None (error)
            success: Whether tool succeeded
            error: Error message if failed
        """
        return Event(
            type="tool_end",
            timestamp=self._now(),
            payload={
                "session_id": self.session_id,
                "tool_call_id": tool_call_id,
                "success": success,
                "tool_output": tool_output,
                "error": error,
            },
        )

    def message_end(
        self,
        message: dict[str, t.Any],
        usage: Usage,
        cost_usd: str,
    ) -> Event:
        """
        Emit message_end event.

        When: Complete assistant message received (streaming complete)

        Args:
            message: Full message object
            usage: Token usage for this message
            cost_usd: Cost for this message
        """
        return Event(
            type="message_end",
            timestamp=self._now(),
            payload={
                "session_id": self.session_id,
                "message": message,
                "usage": usage.to_dict(),
                "cost_usd": cost_usd,
            },
        )

    def settled(self, state: GithiAgentState) -> Event:
        """
        Emit settled event.

        When: No pending work (not streaming, not compacting, no pending messages).
        Safe to checkpoint.

        Args:
            state: Current settled state
        """
        return Event(
            type="settled",
            timestamp=self._now(),
            payload={
                "session_id": self.session_id,
                "is_settled": state.is_settled,
                "pending_count": state.pending_count,
            },
        )

    def error(
        self,
        error_type: str,
        error_message: str,
        *,
        recoverable: bool = False,
        context: dict[str, t.Any] | None = None,
    ) -> Event:
        """
        Emit error event.

        When: Exception occurs

        Args:
            error_type: Exception class name
            error_message: Error description
            recoverable: Whether error is recoverable
            context: Additional error context
        """
        payload: dict[str, t.Any] = {
            "session_id": self.session_id,
            "error_type": error_type,
            "error_message": error_message,
            "recoverable": recoverable,
        }

        if context:
            payload["context"] = context

        return Event(
            type="error",
            timestamp=self._now(),
            payload=payload,
        )

    # === Githi Extension Events (contracts/githi_events.md) ===

    def compaction_start(
        self,
        reason: str,
        tokens_before: int,
        *,
        first_kept_entry_id: str | None = None,
        strategy: str = "summarize",
        compaction_id: str | None = None,
    ) -> Event:
        """
        Emit compaction_start event.

        When: Context window limit reached, summarization begins.
        Must pair with compaction_end (same compaction_id).

        Args:
            reason: "context_limit" | "manual"
            tokens_before: Token count before compaction
            first_kept_entry_id: First message kept in full
            strategy: "summarize" | "truncate"
            compaction_id: Unique ID for pairing (auto-generated if not provided)
        """
        comp_id = compaction_id or f"comp-{uuid.uuid4().hex[:8]}"

        return Event(
            type="compaction_start",
            timestamp=self._now(),
            payload={
                "session_id": self.session_id,
                "compaction_id": comp_id,
                "reason": reason,
                "tokens_before": tokens_before,
                "first_kept_entry_id": first_kept_entry_id,
                "strategy": strategy,
            },
        )

    def compaction_end(
        self,
        compaction_id: str,
        stop_reason: str,
        *,
        tokens_after: int | None = None,
        summary_digest: str | None = None,
        error: str | None = None,
    ) -> Event:
        """
        Emit compaction_end event.

        When: Summarization completes (success or failure).
        Must pair with compaction_start (same compaction_id).

        Args:
            compaction_id: Same ID from compaction_start
            stop_reason: "completed" | "error" | "timeout"
            tokens_after: Token count after (if completed)
            summary_digest: Blake3 hash of summary (for verification)
            error: Error message if failed
        """
        return Event(
            type="compaction_end",
            timestamp=self._now(),
            payload={
                "session_id": self.session_id,
                "compaction_id": compaction_id,
                "stop_reason": stop_reason,
                "tokens_after": tokens_after,
                "summary_digest": summary_digest,
                "error": error,
            },
        )

    def skill_invoked(
        self,
        skill_name: str,
        args: dict[str, t.Any],
        *,
        source: str = "user",
        skill_id: str | None = None,
    ) -> Event:
        """
        Emit skill_invoked event.

        When: Skill (slash command) executes

        Args:
            skill_name: Name of skill executed
            args: Arguments passed
            source: "user" | "agent"
            skill_id: Unique execution ID (auto-generated if not provided)
        """
        sk_id = skill_id or f"skill-{uuid.uuid4().hex[:8]}"

        return Event(
            type="skill_invoked",
            timestamp=self._now(),
            payload={
                "session_id": self.session_id,
                "skill_id": sk_id,
                "skill_name": skill_name,
                "args": args,
                "source": source,
            },
        )

    def message_chunk(
        self,
        delta: str,
        chunk_type: str,
        index: int,
        *,
        is_final: bool = False,
    ) -> Event:
        """
        Emit message_chunk event (streaming).

        When: Streaming delta received from LLM.

        NOTE: NOT persisted to journal (ephemeral, frontend-only).
        Authoritative record is message_end.

        Args:
            delta: Text delta
            chunk_type: "text" | "tool_call" | "thinking"
            index: Chunk sequence number
            is_final: Last chunk flag
        """
        return Event(
            type="message_chunk",
            timestamp=self._now(),
            payload={
                "session_id": self.session_id,
                "delta": delta,
                "chunk_type": chunk_type,
                "index": index,
                "is_final": is_final,
            },
        )

    def memory_recall(
        self,
        block_id: str,
        source: str,
        token_estimate: int,
        *,
        injected: bool = True,
        relevance_score: float = 1.0,
    ) -> Event:
        """
        Emit memory_recall event (Phase 3).

        When: Memory system injects content into context

        Args:
            block_id: Memory block ID
            source: "strategy" | "curated" | "session"
            token_estimate: Estimated tokens injected
            injected: Actually injected (vs filtered)
            relevance_score: 0.0-1.0 relevance
        """
        return Event(
            type="memory_recall",
            timestamp=self._now(),
            payload={
                "session_id": self.session_id,
                "block_id": block_id,
                "source": source,
                "token_estimate": token_estimate,
                "injected": injected,
                "relevance_score": relevance_score,
            },
        )

    def session_branch_created(
        self,
        parent_session_id: str,
        branch_session_id: str,
        branch_point_index: int,
        *,
        reason: str = "user_request",
    ) -> Event:
        """
        Emit session_branch_created event.

        When: Session branches (forked conversation)

        Args:
            parent_session_id: Parent session
            branch_session_id: New branch session
            branch_point_index: Message index where branched
            reason: "user_request" | "retry" | "exploration"
        """
        return Event(
            type="session_branch_created",
            timestamp=self._now(),
            payload={
                "parent_session_id": parent_session_id,
                "branch_session_id": branch_session_id,
                "branch_point_index": branch_point_index,
                "reason": reason,
            },
        )


# === Event Pairing Helpers ===

class EventPairer:
    """
    Helper for ensuring event pairing integrity.

    Tracks start events and ensures they pair with end events.
    Critical for crash recovery detection.

    Pattern: Stateful helper class (PYTHON_PATTERNS_MASTERCLASS.md Section 4.3)
    """

    def __init__(self) -> None:
        """Initialize event pairer"""
        self._tool_calls: set[str] = set()
        self._compactions: set[str] = set()

    def register_tool_start(self, tool_call_id: str) -> None:
        """Register tool_start event (expecting tool_end)"""
        self._tool_calls.add(tool_call_id)

    def register_tool_end(self, tool_call_id: str) -> None:
        """Register tool_end event (pairs with tool_start)"""
        self._tool_calls.discard(tool_call_id)

    def register_compaction_start(self, compaction_id: str) -> None:
        """Register compaction_start event (expecting compaction_end)"""
        self._compactions.add(compaction_id)

    def register_compaction_end(self, compaction_id: str) -> None:
        """Register compaction_end event (pairs with compaction_start)"""
        self._compactions.discard(compaction_id)

    def get_orphaned_tool_calls(self) -> set[str]:
        """Get tool_call_ids without matching tool_end (crash during execution)"""
        return self._tool_calls.copy()

    def get_orphaned_compactions(self) -> set[str]:
        """Get compaction_ids without matching compaction_end (crash during compaction)"""
        return self._compactions.copy()

    def has_orphans(self) -> bool:
        """Check if any orphaned events exist"""
        return bool(self._tool_calls or self._compactions)
