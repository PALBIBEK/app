"""
Context compaction for Githi runtime.

Manages context window through summarization:
- Token counting
- Compaction trigger detection
- Summary generation
- Message pruning
- Compaction events

Ported from githi/src/githi/harness/compaction.py with runtime adaptations.

Pattern: Service class with LLM integration (PYTHON_PATTERNS_MASTERCLASS.md)
Phase: Phase 1 - Basic summarization
"""

from __future__ import annotations

import typing as t
from decimal import Decimal
from collections.abc import AsyncIterator
import hashlib
from typing import Protocol, Callable

if t.TYPE_CHECKING:
    from supervisor.agent.runtimes.githi.state import GithiAgentState, Usage
    from supervisor.agent.runtimes.githi.events import GithiEventEmitter, Event
    from supervisor.agent.base import Message, ModelGateway


# === Constants from harness/compaction.py ===

CHARS_PER_TOKEN = 3.5
"""Rough English approximation. Deliberately not a real tokenizer: this
decides *when* to compact, and being 20% off simply moves the trigger point
slightly. A tokenizer dependency would cost far more than that error."""

DEFAULT_LIMIT_TOKENS = 120_000
KEEP_RECENT_MESSAGES = 8
COMPACTION_NOTICE = (
    "[Earlier turns were compacted away to stay within the context budget. "
    "Their tool output is no longer visible -- if you need details from before "
    "this point, re-derive them rather than recalling them.]"
)

SUMMARY_MARKER = "[context checkpoint]"
"""Prefix on the injected summary. It is how a later compaction recognizes its
own previous output and updates it rather than summarizing it again."""


class Summarizer(Protocol):
    """Turns the span being dropped into a structured checkpoint.

    Deliberately a plain callable rather than an LLM client: compaction stays
    a pure function of its inputs, tests run offline, and the caller decides
    which (cheap) model does the summarizing.
    """

    def __call__(
        self, messages: list[dict], previous_summary: str | None = None
    ) -> str:
        ...


SUMMARIZATION_SYSTEM_PROMPT = (
    "You are a context summarization assistant. Your task is to read a "
    "conversation between a user and an AI assistant, then produce a structured "
    "summary following the exact format specified.\n\n"
    "Do NOT continue the conversation. Do NOT respond to any questions in the "
    "conversation. ONLY output the structured summary."
)

_FORMAT = """## Goal
[What is the user trying to accomplish? Can be multiple items.]

## Constraints & Preferences
- [Constraints, preferences or requirements the user stated, or "(none)"]

## Progress
### Done
- [x] [Completed tasks/changes]

### In Progress
- [ ] [Current work]

### Blocked
- [Issues preventing progress, if any]

## Key Decisions
- **[Decision]**: [Brief rationale]

## Next Steps
1. [Ordered list of what should happen next]

## Critical Context
- [Data, examples or references needed to continue, or "(none)"]

Keep each section concise. Preserve exact file paths, function names, and error
messages."""

SUMMARIZATION_PROMPT = (
    "The messages above are a conversation to summarize. Create a structured "
    "context checkpoint that another LLM will use to continue the work.\n\n"
    "Use this EXACT format:\n\n" + _FORMAT
)

UPDATE_SUMMARIZATION_PROMPT = (
    "The messages above are NEW conversation messages to incorporate into the "
    "existing summary in <previous-summary> tags.\n\n"
    "Update the existing summary with the new information. RULES:\n"
    "- PRESERVE all existing information from the previous summary\n"
    "- ADD new progress, decisions and context from the new messages\n"
    "- UPDATE Progress: move items from \"In Progress\" to \"Done\" when completed\n"
    "- UPDATE \"Next Steps\" based on what was accomplished\n"
    "- PRESERVE exact file paths, function names and error messages\n"
    "- If something is no longer relevant, you may remove it\n\n"
    "Use this EXACT format:\n\n" + _FORMAT
)


class CompactionError(Exception):
    """Base exception for compaction errors"""

    pass


# === Pure functions for compaction logic ===


def estimate_tokens(messages: list[dict]) -> int:
    """Estimate token count for messages.

    Args:
        messages: List of message dicts

    Returns:
        Estimated token count
    """
    total = 0
    for m in messages:
        content = m.get("content", "")
        if isinstance(content, str):
            total += len(content)
        elif isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and part.get("type") == "text":
                    total += len(part.get("text", ""))
        for call in m.get("tool_calls") or []:
            total += len(str(call))
    return int(total / CHARS_PER_TOKEN)


def needs_compaction(
    messages: list[dict], limit_tokens: int = DEFAULT_LIMIT_TOKENS
) -> bool:
    """Check if compaction is needed."""
    return estimate_tokens(messages) > limit_tokens


def extract_previous_summary(messages: list[dict]) -> str | None:
    """Find a checkpoint this module injected earlier.

    Without this a long session summarizes its own summary repeatedly, and
    detail decays geometrically -- by the third compaction the original goal
    has been paraphrased three times.
    """
    for message in reversed(messages):
        content = message.get("content", "")
        if isinstance(content, str) and content.startswith(SUMMARY_MARKER):
            return content[len(SUMMARY_MARKER) :].strip()
    return None


def build_summary_request(
    messages: list[dict], previous_summary: str | None = None
) -> list[dict]:
    """Build the message list to send to the summarizing model.

    The instruction goes *after* the conversation rather than in the system
    prompt so the model reads the transcript as data and the ask as the most
    recent thing said.
    """
    request: list[dict] = [
        {"role": "system", "content": SUMMARIZATION_SYSTEM_PROMPT},
        *messages,
    ]
    if previous_summary:
        request.append(
            {
                "role": "user",
                "content": f"<previous-summary>\n{previous_summary}\n</previous-summary>\n\n"
                + UPDATE_SUMMARIZATION_PROMPT,
            }
        )
    else:
        request.append({"role": "user", "content": SUMMARIZATION_PROMPT})
    return request


def compact(
    messages: list[dict],
    limit_tokens: int = DEFAULT_LIMIT_TOKENS,
    summarizer: Summarizer | None = None,
) -> list[dict]:
    """Drop the middle of the transcript, in whole exchanges.

    Kept: every system message, the first user message (the original
    instruction -- losing it means forgetting the task), and the most recent
    exchanges. Everything between is replaced by a structured summary when a
    `summarizer` is supplied, or a bare notice when it is not.

    The summary is the difference between compaction that costs the agent its
    findings and compaction that costs it only the transcript that produced
    them. 40 messages of tool output usually reduce to five lines of
    conclusions, which is exactly what the next turn needs.
    """
    if not needs_compaction(messages, limit_tokens):
        return messages

    system = [m for m in messages if m.get("role") == "system"]
    rest = [m for m in messages if m.get("role") != "system"]

    first_user = next((m for m in rest if m.get("role") == "user"), None)
    head: list[dict] = [first_user] if first_user else []

    tail = _whole_exchanges(rest[-KEEP_RECENT_MESSAGES:], rest)
    if first_user in tail:
        head = []

    # Already minimal
    if len(head) + len(tail) >= len(rest):
        return messages

    head_end = (
        next((i + 1 for i, m in enumerate(rest) if m is first_user), 0) if head else 0
    )
    dropped = rest[head_end : len(rest) - len(tail)]
    notice = _checkpoint(dropped, messages, summarizer)
    out = [*system, *head, notice, *tail]

    # Last resort: shrink large tool results
    if needs_compaction(out, limit_tokens):
        out = [_shrink(m, limit_tokens) for m in out]
    return out


def _checkpoint(
    dropped: list[dict], full: list[dict], summarizer: Summarizer | None
) -> dict:
    """The message that replaces the dropped span."""
    if summarizer is None or not dropped:
        return {"role": "user", "content": COMPACTION_NOTICE}

    try:
        summary = summarizer(dropped, previous_summary=extract_previous_summary(full))
    except Exception:  # noqa: BLE001
        return {"role": "user", "content": COMPACTION_NOTICE}

    if not summary or not summary.strip():
        return {"role": "user", "content": COMPACTION_NOTICE}
    return {"role": "user", "content": f"{SUMMARY_MARKER}\n{summary.strip()}"}


def _whole_exchanges(window: list[dict], full: list[dict]) -> list[dict]:
    """Extend `window` backwards until no tool result is orphaned."""
    if not window:
        return []

    start = len(full) - len(window)
    while start > 0:
        first = full[start]
        if first.get("role") != "tool":
            break
        start -= 1

    kept = full[start:]

    # Check for orphaned assistant tool calls
    result_ids = {m.get("tool_call_id") for m in kept if m.get("role") == "tool"}
    while kept and kept[-1].get("role") == "assistant" and kept[-1].get("tool_calls"):
        if all(c.get("id") in result_ids for c in kept[-1].get("tool_calls", [])):
            break
        kept = kept[:-1]
    return kept


def _shrink(message: dict, limit_tokens: int) -> dict:
    """Shrink large tool results."""
    if message.get("role") != "tool":
        return message
    content = message.get("content", "")
    budget = max(500, int(limit_tokens * CHARS_PER_TOKEN / 10))
    if len(content) <= budget:
        return message
    return {**message, "content": content[:budget] + "\n[truncated]"}


def build_summarizer(gateway) -> Summarizer:
    """Wire a `Summarizer` to a model gateway.

    No tools are offered. The summarizer has one job, and a model given tools
    while looking at a transcript full of tool calls will start making them --
    turning a cheap single call into a second agent loop.
    """

    def summarize(messages: list[dict], previous_summary: str | None = None) -> str:
        request = build_summary_request(messages, previous_summary)
        completion = gateway.complete(request, tools=[], model=None)
        msg = getattr(completion, "message", None)
        return getattr(msg, "content", "") or ""

    return summarize


class CompactionService:
    """
    Context compaction service.

    Manages context window by summarizing old messages when token limit reached.

    Strategy (Phase 1):
    1. Detect when context exceeds limit (128k tokens)
    2. Emit compaction_start event
    3. Summarize old messages (keep recent N messages)
    4. Replace old messages with summary
    5. Emit compaction_end event

    Pattern: Service class with event emission

    Example:
        compactor = CompactionService(
            model_gateway=gateway,
            emitter=emitter,
            limit_tokens=128000,
            keep_recent_messages=10
        )

        compacted, events = await compactor.compact_if_needed(
            messages=message_history,
            state=state
        )
    """

    def __init__(
        self,
        model_gateway: ModelGateway,
        emitter: GithiEventEmitter,
        *,
        limit_tokens: int = 128000,
        keep_recent_messages: int = 10,
        summary_max_tokens: int = 8000,
        strategy: str = "summarize",
    ) -> None:
        """
        Initialize compaction service.

        Args:
            model_gateway: LLM API for generating summaries
            emitter: Event emitter
            limit_tokens: Token limit to trigger compaction
            keep_recent_messages: Number of recent messages to keep in full
            summary_max_tokens: Max tokens for summary
            strategy: "summarize" | "truncate"
        """
        self.model_gateway = model_gateway
        self.emitter = emitter
        self.limit_tokens = limit_tokens
        self.keep_recent_messages = keep_recent_messages
        self.summary_max_tokens = summary_max_tokens
        self.strategy = strategy

    async def compact_if_needed(
        self,
        messages: list[Message],
        state: GithiAgentState,
    ) -> tuple[list[Message], list[Event], Usage, Decimal]:
        """
        Compact context if token limit exceeded.

        Args:
            messages: Current message history
            state: Current session state

        Returns:
            Tuple of (compacted_messages, events, usage, cost)
            - compacted_messages: New message list (same if no compaction)
            - events: Events emitted during compaction
            - usage: Token usage for compaction (if any)
            - cost: Cost of compaction (if any)
        """
        # Estimate token count
        token_count = self._estimate_tokens(messages)

        # Check if compaction needed
        if token_count < self.limit_tokens:
            # No compaction needed
            return messages, [], Usage(), Decimal("0.00")

        # Compaction needed
        events = []

        # Emit compaction_start event
        compaction_id = f"comp-{state.compaction_count + 1}"
        first_kept_index = max(0, len(messages) - self.keep_recent_messages)
        first_kept_entry_id = (
            getattr(messages[first_kept_index], "metadata", None)
            and messages[first_kept_index].metadata.message_id
        ) or f"msg-{first_kept_index}"

        compaction_start_event = self.emitter.compaction_start(
            reason="context_limit",
            tokens_before=token_count,
            first_kept_entry_id=first_kept_entry_id,
            strategy=self.strategy,
            compaction_id=compaction_id,
        )
        events.append(compaction_start_event)

        # Perform compaction
        try:
            compacted, usage, cost = await self._compact(
                messages=messages,
                state=state,
                compaction_id=compaction_id,
            )

            # Estimate tokens after compaction
            tokens_after = self._estimate_tokens(compacted)

            # Generate summary digest (Blake3 hash)
            summary_digest = self._compute_summary_digest(compacted)

            # Emit compaction_end event (success)
            compaction_end_event = self.emitter.compaction_end(
                compaction_id=compaction_id,
                stop_reason="completed",
                tokens_after=tokens_after,
                summary_digest=summary_digest,
                error=None,
            )
            events.append(compaction_end_event)

            return compacted, events, usage, cost

        except Exception as e:
            # Compaction failed
            compaction_end_event = self.emitter.compaction_end(
                compaction_id=compaction_id,
                stop_reason="error",
                tokens_after=None,
                summary_digest=None,
                error=str(e),
            )
            events.append(compaction_end_event)

            # Return original messages (no compaction)
            return messages, events, Usage(), Decimal("0.00")

    async def _compact(
        self,
        messages: list[Message],
        state: GithiAgentState,
        compaction_id: str,
    ) -> tuple[list[Message], Usage, Decimal]:
        """
        Perform compaction.

        Args:
            messages: Message history
            state: Current state
            compaction_id: Compaction ID for logging

        Returns:
            Tuple of (compacted_messages, usage, cost)
        """
        if self.strategy == "summarize":
            return await self._compact_summarize(messages, state)
        elif self.strategy == "truncate":
            return await self._compact_truncate(messages, state)
        else:
            raise CompactionError(f"Unknown compaction strategy: {self.strategy}")

    async def _compact_summarize(
        self,
        messages: list[Message],
        state: GithiAgentState,
    ) -> tuple[list[Message], Usage, Decimal]:
        """
        Compact by summarizing old messages.

        Strategy:
        1. Split messages into: old (to summarize) + recent (keep in full)
        2. Generate summary of old messages via LLM
        3. Return: [summary_message] + recent_messages

        Args:
            messages: Message history
            state: Current state

        Returns:
            Tuple of (compacted_messages, usage, cost)
        """
        # Split messages
        split_index = max(0, len(messages) - self.keep_recent_messages)
        old_messages = messages[:split_index]
        recent_messages = messages[split_index:]

        if not old_messages:
            # Nothing to summarize
            return messages, Usage(), Decimal("0.00")

        # Build prompt for summarization
        summary_prompt = self._build_summary_prompt(old_messages)

        # Call LLM to generate summary
        summary_response = await self.model_gateway.complete(
            model=state.model,
            messages=[
                Message(role="system", content="You are a conversation summarizer."),
                Message(role="user", content=summary_prompt),
            ],
            max_tokens=self.summary_max_tokens,
            temperature=0.3,  # Lower temp for factual summary
        )

        summary_text = summary_response.message.content
        usage = summary_response.usage
        cost = summary_response.cost_usd

        # Create summary message
        summary_message = Message(
            role="system",
            content=f"[Summary of {len(old_messages)} previous messages]\n\n{summary_text}",
        )

        # Return compacted messages
        compacted = [summary_message] + recent_messages

        return compacted, usage, cost

    async def _compact_truncate(
        self,
        messages: list[Message],
        state: GithiAgentState,
    ) -> tuple[list[Message], Usage, Decimal]:
        """
        Compact by truncating old messages.

        Simply drops old messages, keeping only recent N.

        Args:
            messages: Message history
            state: Current state

        Returns:
            Tuple of (compacted_messages, usage, cost)
        """
        # Keep only recent messages
        recent_messages = messages[-self.keep_recent_messages :]

        # Add truncation notice
        truncation_message = Message(
            role="system",
            content=f"[{len(messages) - len(recent_messages)} earlier messages truncated]",
        )

        compacted = [truncation_message] + recent_messages

        return compacted, Usage(), Decimal("0.00")

    def _build_summary_prompt(self, messages: list[Message]) -> str:
        """
        Build prompt for summarization.

        Args:
            messages: Messages to summarize

        Returns:
            Prompt string
        """
        # Format messages for summarization
        formatted = []
        for msg in messages:
            role = msg.role.upper()
            content = msg.content

            # Truncate long content
            if len(content) > 500:
                content = content[:500] + "..."

            formatted.append(f"{role}: {content}")

        conversation = "\n\n".join(formatted)

        prompt = f"""Please provide a concise summary of the following conversation.
Focus on:
- Key decisions made
- Important information exchanged
- Tools used and their results
- Current state/context

Conversation:
{conversation}

Summary (be concise, ~{self.summary_max_tokens // 4} words max):"""

        return prompt

    def _estimate_tokens(self, messages: list[Message]) -> int:
        """
        Estimate token count for messages.

        Phase 1: Rough estimate (4 chars = 1 token)
        Phase 2: Use tiktoken for accurate counting

        Args:
            messages: Messages to count

        Returns:
            Estimated token count
        """
        total_chars = 0

        for msg in messages:
            total_chars += len(msg.content)

            # Add overhead for role, metadata, etc.
            total_chars += 20

            # Add tool call overhead
            if hasattr(msg, "tool_calls") and msg.tool_calls:
                for call in msg.tool_calls:
                    total_chars += len(call.name) + 50
                    # Estimate argument size
                    import json

                    total_chars += len(json.dumps(call.arguments))

        # Rough estimate: 4 characters per token
        return total_chars // 4

    def _compute_summary_digest(self, messages: list[Message]) -> str:
        """
        Compute Blake3 hash of compacted messages.

        For verification that compaction completed correctly.

        Args:
            messages: Compacted messages

        Returns:
            Blake3 hash (hex string)
        """
        # Phase 1: Use SHA256 (Blake3 not in stdlib)
        # Phase 2: Use Blake3 for faster hashing

        content = "".join(msg.content for msg in messages)
        hash_obj = hashlib.sha256(content.encode())
        return f"sha256:{hash_obj.hexdigest()[:16]}"


# === Type Stubs for Protocol Compliance ===

if not t.TYPE_CHECKING:

    class Message:
        """Message object"""

        def __init__(
            self,
            role: str,
            content: str,
            *,
            tool_calls: list | None = None,
            metadata: t.Any = None,
        ):
            self.role = role
            self.content = content
            self.tool_calls = tool_calls or []
            self.metadata = metadata

    class ModelGateway:
        """Model gateway protocol"""

        async def complete(
            self,
            model: str,
            messages: list[Message],
            max_tokens: int,
            temperature: float,
        ):
            """Non-streaming completion"""
            raise NotImplementedError

    class Usage:
        """Usage tracking"""

        def __init__(
            self,
            input_tokens: int = 0,
            output_tokens: int = 0,
            cache_read_tokens: int = 0,
            cache_write_tokens: int = 0,
        ):
            self.input_tokens = input_tokens
            self.output_tokens = output_tokens
            self.cache_read_tokens = cache_read_tokens
            self.cache_write_tokens = cache_write_tokens
