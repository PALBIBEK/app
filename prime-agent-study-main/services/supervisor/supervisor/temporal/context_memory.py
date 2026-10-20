"""The pinned memory profile applied to stepped turns: compaction and recall.

A session pins a ``MemoryProfile`` (compaction policy, recall backend), which
binds to its ``context_memory`` seam. Every stepped turn consults it:

* **Compaction**, between turns. When the carried-over conversation passes the
  profile's limit, measured against the model's context window (pinned on the
  profile, else as the gateway reports it for the session's model), the seam
  summarises everything but the most recent
  ``keep_recent_tokens`` (whole tool exchanges only), and the compacted
  conversation is journalled on ``CompactionEnd`` as the new checkpoint later
  turns build on.
* **Recall**, on a run's first model step. Whatever the seam recalls for the
  user's message is journalled on ``MemoryRecall`` and shown to the model
  ahead of that message.

Both are keyed by run, so a retried or re-homed activity reuses the journalled
result instead of summarising or recalling twice.
"""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final, Protocol

from supervisor.agent.events import (
    CompactionEnd,
    CompactionStart,
    Envelope,
    MemoryRecall,
    MessageEnd,
    ToolEnd,
    ToolStart,
)
from supervisor.memory.binding import long_term_agent
from supervisor.memory.history import message_text
from supervisor.models.messages import fits_with_reply
from supervisor.seams import CompactionPreparation, MemoryInvokeContext, ModelCatalog
from supervisor.seams.compaction import (
    COMPACTION_NOTICE,
    extract_previous_summary,
    whole_exchanges,
)
from supervisor.session.workspaces import session_workspace

if TYPE_CHECKING:
    from supervisor.catalog import MemoryProfile
    from supervisor.seams import ContextMemoryProvider
    from supervisor.session import SessionManager

__all__ = ["DEFAULT_CONTEXT_WINDOW", "PinnedContextMemory"]

DEFAULT_CONTEXT_WINDOW: Final = 200_000
"""Last resort: neither the memory profile nor the gateway says how much the model holds."""

type Record = Callable[[str, str, str, Any], Awaitable[int]]
"""``(session_id, run_id, key, event)``: journal ``event`` under a run-scoped key."""


class Keyed(Protocol):
    def __call__(self, session_id: str, run_id: str, key: str) -> Awaitable[Any | None]: ...


@dataclass(frozen=True, slots=True)
class _Binding:
    """What a session pinned for its memory, resolved for one call."""

    profile: MemoryProfile
    memory: ContextMemoryProvider
    context: MemoryInvokeContext
    gateway: object
    model: str | None

    async def context_window(self) -> int:
        """The model's window: pinned on the profile, else reported by the gateway."""
        if self.profile.context_window:
            return self.profile.context_window
        if (
            isinstance(self.gateway, ModelCatalog)
            and self.model
            and (reported := await asyncio.to_thread(self.gateway.context_window, self.model))
        ):
            return reported
        return DEFAULT_CONTEXT_WINDOW

    def reserve(self, window: int) -> int:
        """Room kept for the reply: the profile's, at most a quarter of the window."""
        return min(self.profile.reserve_tokens, int(window * _MAX_RESERVE_SHARE))

    def keep_recent(self, window: int) -> int:
        """Recent conversation kept verbatim: the profile's, at most 30% of the window."""
        return min(self.profile.keep_recent_tokens, int(window * _MAX_KEEP_SHARE))

    def limit(self, window: int) -> int:
        """Compact once the conversation needs more than this many tokens.

        The profile's sizes are absolute (the defaults suit large windows), so they
        are capped to the model's window: unscaled, a 16k reserve on an 8k model
        made the limit negative and a 20k recent span kept everything.
        """
        return min(
            window - self.reserve(window),
            int(window * self.profile.pressure_ratio),
            fits_with_reply(window),  # past it, the next call is refused (ContextWindowFull)
        )


def _reported_tokens(events: Sequence[Envelope], memory: ContextMemoryProvider) -> int:
    """What the next call will send, from the provider's count of the last one.

    The last model call's input and output, plus what the user said since (the
    run being compacted for starts with a new message). 0 with no reported usage.
    """
    last, usage = None, None
    for i, env in enumerate(events):
        p = env.payload
        if isinstance(p, MessageEnd) and p.role == "assistant" and p.usage is not None:
            last, usage = i, p.usage
    if last is None or usage is None:
        return 0
    after = [
        {"role": p.role, "content": p.content}
        for env in events[last + 1 :]
        if isinstance(p := env.payload, MessageEnd)
    ]
    return int(usage.input_tokens + usage.output_tokens) + (memory.estimate_tokens(after) if after else 0)


_MAX_RESERVE_SHARE = 0.25
_MAX_KEEP_SHARE = 0.30


class PinnedContextMemory:
    def __init__(self, manager: SessionManager, *, record: Record, keyed: Keyed) -> None:
        self._manager = manager
        self._record = record
        self._keyed = keyed

    async def _binding(self, session_id: str) -> _Binding | None:
        row = await self._manager._get_session_row(session_id)
        profile = self._manager.memory_profile(row)
        seams = getattr(self._manager._registry.get(session_id), "seams", None)
        memory = getattr(seams, "context_memory", None)
        if profile is None or memory is None:
            return None
        context = MemoryInvokeContext(
            session_id=session_id,
            tenant_id=row.tenant_id,
            workspace_path=str(session_workspace(session_id)),
            owner=row.owner_principal_id or row.tenant_id,
            agent=long_term_agent(
                composition_id=(row.composition_pins or {}).get("composition_id"),
                harness_version=row.harness_version,
            ),
        )
        return _Binding(
            profile,
            memory,
            context,
            gateway=getattr(seams, "model_gateway", None),
            model=self._manager.session_model(row),
        )

    # --- compaction ----------------------------------------------------------------

    async def compact(
        self,
        session_id: str,
        run_id: str,
        prior: list[dict[str, Any]],
        events: Sequence[Envelope] = (),
    ) -> None:
        """Compact the conversation carried into this run, if the profile's limit is passed.

        ``events``: the journal the audience may see. The summary names the entries
        it replaced, so the model can reopen any of them verbatim with ``history``.
        """
        if await self._keyed(session_id, run_id, "compaction:end") is not None:
            return
        binding = await self._binding(session_id)
        if binding is None or not binding.profile.compaction_enabled:
            return
        # The estimate sees only the conversation; the model also gets the system
        # prompt, the tool schemas and recalled memory. The provider's own count of
        # the last call includes all of it, so the larger of the two decides.
        tokens = max(binding.memory.estimate_tokens(prior), _reported_tokens(events, binding.memory))
        window = await binding.context_window()
        if tokens <= binding.limit(window):
            return
        tail = _recent(prior, binding.keep_recent(window), binding.memory)
        head = prior[: len(prior) - len(tail)]
        if not head:
            return

        compaction_id = f"cmp-{run_id[:12]}"
        await self._record(
            session_id,
            run_id,
            "compaction:start",
            CompactionStart(
                compaction_id=compaction_id,
                reason="threshold",
                tokens_before=tokens,
                first_kept_entry_id=str(len(head)),
            ),
        )
        preparation = CompactionPreparation(
            first_kept_entry_id=str(len(head)),
            messages_to_summarize=head,
            tokens_before=tokens,
            previous_summary=extract_previous_summary(prior),
            context_window=window,
            reserve_tokens=binding.reserve(window),
        )
        replaced = _replaced_range([e for e in events if e.turn_id != run_id], tail)
        end = await self._summarise(compaction_id, binding, preparation, tail, replaced)
        await self._record(session_id, run_id, "compaction:end", end)

    @staticmethod
    async def _summarise(
        compaction_id: str,
        binding: _Binding,
        preparation: CompactionPreparation,
        tail: list[dict[str, Any]],
        replaced: tuple[int, int] | None = None,
    ) -> CompactionEnd:
        try:
            decision = await asyncio.to_thread(
                binding.memory.before_compact, binding.context, preparation, reason="threshold"
            )
        except Exception:  # noqa: BLE001 — a failed summary leaves the turn uncompacted, not failed
            return CompactionEnd(compaction_id=compaction_id, stop_reason="error")
        if decision.action == "cancel":
            return CompactionEnd(compaction_id=compaction_id, stop_reason="cancelled")
        # "proceed_default": the provider defers to the platform, which drops the span
        # behind a plain notice so the conversation still fits.
        summary = (
            decision.result.summary
            if decision.action == "custom" and decision.result is not None
            else COMPACTION_NOTICE
        )
        if replaced is not None:
            summary = f"{summary}\n\n{_citation(*replaced)}"
        compacted = [{"role": "user", "content": summary}, *tail]
        return CompactionEnd(
            compaction_id=compaction_id,
            stop_reason="ok",
            tokens_after=binding.memory.estimate_tokens(compacted),
            summary_digest=hashlib.sha256(summary.encode()).hexdigest()[:32],
            messages=compacted,
            replaced_from_seq=replaced[0] if replaced is not None else None,
            replaced_to_seq=replaced[1] if replaced is not None else None,
        )

    # --- recall ------------------------------------------------------------------------

    async def recall(self, session_id: str, run_id: str, user_text: str) -> None:
        """Journal what the pinned recall backend has for this run's message."""
        if await self._keyed(session_id, run_id, "recall") is not None:
            return
        binding = await self._binding(session_id)
        if binding is None or not binding.profile.recall_enabled or not user_text.strip():
            return
        try:
            block = await asyncio.to_thread(
                binding.memory.recall_for_turn, binding.context, user_prompt=user_text
            )
        except Exception:  # noqa: BLE001 — recall is best effort; the turn goes on without it
            block = None
        text = (block.text if block is not None else "").strip()
        await self._record(
            session_id,
            run_id,
            "recall",
            MemoryRecall(
                block_id=f"rec-{run_id[:12]}",
                source=block.source if block is not None else "semantic",
                token_estimate=binding.memory.estimate_tokens([{"role": "system", "content": text}])
                if text
                else 0,
                injected=bool(text),
                relevance_score=block.score if block is not None else None,
                text=text or None,
            ),
        )


def _citation(first: int, last: int) -> str:
    return (
        f"[This summary stands in for journal entries #{first}–#{last}, which are kept verbatim. "
        f"Reopen any of them with the `history` tool: `seq` for one entry, `from_seq`={first} and "
        f"`to_seq`={last} for the range, `call_id` for a tool call with its result.]"
    )


def _text(message: Mapping[str, Any]) -> str:
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            str(part.get("text", "")) for part in content if isinstance(part, dict) and part.get("type") == "text"
        )
    return ""


def _replaced_range(before: Sequence[Envelope], tail: list[dict[str, Any]]) -> tuple[int, int] | None:
    """The seqs of the entries the summarised head came from: up to where the kept tail starts.

    The tail is found by its first message's role and text, newest match first; if
    it cannot be found the whole carried-over journal is named (an over-wide range
    is still a correct pointer: everything in it is readable).
    """
    entries = [e for e in before if isinstance(e.payload, (MessageEnd, ToolStart, ToolEnd))]
    if not entries:
        return None
    first, last = entries[0].seq, entries[-1].seq
    if tail:
        role, text = tail[0].get("role"), _text(tail[0]).strip()
        for envelope in reversed(entries):
            event = envelope.payload
            if isinstance(event, MessageEnd) and event.role == role and message_text(event).strip() == text:
                last = envelope.seq - 1
                break
    return (first, last) if last >= first else None


def _recent(
    messages: list[dict[str, Any]],
    keep_tokens: int,
    memory: ContextMemoryProvider,
) -> list[dict[str, Any]]:
    """The newest messages worth ``keep_tokens``, widened so no tool result is orphaned.

    A newest message larger than the whole budget is summarised with the rest
    rather than kept: kept, one oversized reply left the conversation above the
    window after compaction.
    """
    kept: list[dict[str, Any]] = []
    for message in reversed(messages):
        if memory.estimate_tokens([*kept, message]) > keep_tokens:
            break
        kept.insert(0, message)
    return whole_exchanges(kept, messages)
