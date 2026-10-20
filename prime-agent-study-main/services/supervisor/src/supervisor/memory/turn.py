"""Memory inside a session's turns: checkpoint, recall, tools and automatic capture.

Everything here is derived from the session row and its journal, so any worker can
run any step, and every journalled decision is keyed per run so a retried step
reuses it (a replayed model step sees exactly the memory the first attempt saw).

    run start   -> checkpoint()   MemoryContext: audience fingerprint + replay cutoff
    model step 0-> recall()       MemoryInjected: QM delta of the recalled facts
    tool call   -> tools()        TurnMemory: memory / history / sessions tools
    after turns -> capture()      extraction of a quiet burst of exchanges
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any, Final

from supervisor.agent.events import (
    CompactionEnd,
    Envelope,
    MemoryInjected,
    MessageEnd,
    RunEnd,
    ToolStart,
)
from supervisor.journal.reader import JournalReader
from supervisor.journal.writer import keyed_event_id
from supervisor.memory import boundary
from supervisor.memory.binding import CaptureMode, MemoryBinding, Strategy
from supervisor.memory.classification import OneShot
from supervisor.memory.cross import recall_across
from supervisor.memory.delta import Shown, held_hashes, recall_delta
from supervisor.memory.disclosure import Audience
from supervisor.memory.extraction import Exchange, extract_facts
from supervisor.memory.history import message_text
from supervisor.memory.platform import MemoryPlatform
from supervisor.memory.service import CaptureContext, MemoryService
from supervisor.memory.tools import PLATFORM_TOOLS, SessionsTool, TurnMemory
from supervisor.memory.types import UNTRACKED, Provenance, RecallContext, personal_source

logger = logging.getLogger(__name__)

# Keyed journal writes: (session_id, run_id, key, event) -> seq.
type Record = Callable[[str, str, str, Any], Awaitable[int]]

AGENT_ONLY_PROMPT_LINES: Final = (
    "Nothing is saved to memory automatically — you are its sole curator, through the `memory` tool.",
    'If you don\'t save a fact (`memory` action "add"), it is gone when this conversation ends.',
    (
        'Update, don\'t duplicate: when a fact changes, fix the existing fact with action "edit" '
        "instead of adding a contradicting one."
    ),
    'Delete memories you discover are wrong, stale, or no longer useful (action "delete").',
    (
        "Don't re-record what's already remembered, and don't save things that only matter to "
        "the current conversation."
    ),
)


def memory_section(text: str, *, shared: bool, strategy: Strategy) -> str:
    """QM's "What you remember" prompt block (empty when there is nothing to say)."""
    lines = list(AGENT_ONLY_PROMPT_LINES) if strategy is Strategy.AGENT_ONLY else []
    if not text.strip() and not lines:
        return ""
    where = "a shared conversation" if shared else "a private conversation"
    # QM: the heading, then (one newline) where the model is and how to treat memories.
    parts = [
        (
            "## What you remember\n"
            f"You're in {where}; use these authorized memories freely, but preferences "
            "tagged with another notebook heading or `(said in …)` apply only there."
        )
    ]
    if lines:
        parts.append("\n".join(lines))
    if text.strip():
        parts.append(text)
    return "\n\n".join(parts)


def recall_query(message: str, events: Sequence[Envelope]) -> str:
    """QM memoryRecallQuery: the last four exchanges' text plus the current message."""
    recent = [
        f"{e.payload.role}: {message_text(e.payload)[-1000:]}"
        for e in events
        if isinstance(e.payload, MessageEnd) and e.payload.role in ("user", "assistant")
    ][-4:]
    body = "\n".join(r for r in recent if r.split(": ", 1)[1])[-2000:]
    return "\n".join(
        p for p in (body, f"Current message: {message[:2000]}" if message else "") if p
    )


def dependencies(events: Sequence[Envelope]) -> tuple[Provenance, ...]:
    """Provenance of everything a turn's context holds (QM captureDependencies).

    Facts recalled into it carry their own provenance; anything a non-memory tool
    brought in is untracked, which makes a capture's provenance unknown.
    """
    deps: dict[tuple[Any, ...], Provenance] = {}
    for envelope in events:
        event = envelope.payload
        if isinstance(event, MemoryInjected):
            for raw in event.shown:
                provenance = Shown.from_dict(raw).provenance or UNTRACKED
                deps[provenance.group_key] = provenance
        elif isinstance(event, ToolStart) and event.tool_name not in PLATFORM_TOOLS:
            deps[UNTRACKED.group_key] = UNTRACKED
    return tuple(deps.values())


@dataclass(frozen=True, slots=True)
class Scope:
    """A session's memory for one step: who it speaks to, what it may use."""

    audience: Audience
    binding: MemoryBinding
    view: MemoryService


class SessionMemory:
    def __init__(
        self,
        platform: MemoryPlatform,
        reader: JournalReader,
        *,
        one_shot_for: Callable[[str], Awaitable[OneShot]],
        sessions_for: Callable[[str, MemoryBinding], SessionsTool | None] | None = None,
    ) -> None:
        self._platform = platform
        self._reader = reader
        self._one_shot_for = one_shot_for
        self._sessions_for = sessions_for

    async def scope(self, session_id: str) -> Scope | None:
        resolved = await self._platform.session_audience(session_id)
        if resolved is None:
            return None
        audience, binding = resolved
        one_shot = await self._one_shot_for(session_id)
        return Scope(audience, binding, self._platform.view(audience, one_shot=one_shot))

    async def bounded(self, session_id: str) -> list[Envelope]:
        return boundary.bounded(await self._reader.read(session_id))

    # --- checkpoint --------------------------------------------------------------

    async def checkpoint(self, session_id: str, run_id: str, record: Record) -> None:
        """Journal this run's audience checkpoint (advancing the cutoff if it changed)."""
        scope = await self.scope(session_id)
        if scope is None:
            return
        events = await self._reader.read(session_id)
        previous = boundary.latest_context(events)
        nxt = boundary.next_context(
            previous, boundary.fingerprint(scope.audience), events[-1].seq if events else -1
        )
        if previous is None or nxt != previous:
            await record(session_id, run_id, "memory_context", nxt)

    async def prior_history(self, session_id: str) -> list[dict[str, Any]]:
        """The conversation carried into the next run, unless the audience cutoff has passed it.

        The latest checkpoint wins: a run's closing messages, or the compacted
        conversation a later compaction replaced them with.
        """
        events = await self._reader.read(session_id)
        through = boundary.cutoff(events)
        checkpoint = next((e for e in reversed(events) if _checkpoint(e.payload) is not None), None)
        if checkpoint is None or checkpoint.seq <= through:
            return []
        return list(_checkpoint(checkpoint.payload) or [])

    # --- recall ------------------------------------------------------------------

    async def recall(
        self, session_id: str, run_id: str, message: str, record: Record
    ) -> MemoryInjected | None:
        """This run's injected memory, computed once (a retry reuses the journalled one).

        Recorded even when there is nothing to say: the record of what the model holds
        is what the next run's delta and this turn's capture provenance stand on.
        """
        key = f"{run_id}:memory"
        existing = await self._reader.event(session_id, keyed_event_id(session_id, key))
        if existing is not None:
            assert isinstance(existing.payload, MemoryInjected)
            return existing.payload
        scope = await self.scope(session_id)
        if scope is None or not scope.binding.recall_notebooks:
            return None
        events = await self.bounded(session_id)
        labels = scope.binding.labels
        recalled = await recall_across(
            scope.view,
            scope.binding.recall_notebooks,
            RecallContext(query=recall_query(message, events), actor=scope.audience.actor),
            labels,
        )
        held = {
            nb: held_hashes(await scope.view.facts(nb)) for nb in scope.binding.recall_notebooks
        }
        delta = recall_delta(recalled, _retained(events), held, labels)
        section = memory_section(
            delta.text,
            shared=scope.audience.target != personal_source(scope.audience.actor),
            strategy=scope.binding.strategy,
        )
        injected = MemoryInjected(run_id, section, [s.to_dict() for s in delta.shown])
        await record(session_id, run_id, "memory", injected)
        return injected

    # --- tools -------------------------------------------------------------------

    async def tools(self, session_id: str) -> TurnMemory:
        scope = await self.scope(session_id)
        events = await self.bounded(session_id)
        if scope is None:
            return TurnMemory(None, None, None, session_id, events)
        return TurnMemory(
            scope.view,
            scope.binding,
            scope.audience,
            session_id,
            events,
            dependencies=dependencies(events),
            sessions=self._sessions_for(session_id, scope.binding) if self._sessions_for else None,
            labels=scope.binding.labels,
        )

    # --- automatic capture -------------------------------------------------------

    async def capture(self, session_id: str, run_ids: Sequence[str]) -> int:
        """Extract and save durable facts from these runs' exchanges (QM per-turn burst)."""
        scope = await self.scope(session_id)
        if scope is None:
            return 0
        binding = scope.binding
        if (
            binding.strategy is not Strategy.PER_TURN
            or binding.capture is not CaptureMode.WRITABLE
            or binding.writable is None
        ):
            return 0
        events = await self._reader.read(session_id)
        visible = boundary.bounded(events)
        exchanges, runs = [], []
        for run_id in run_ids:
            run = [e for e in visible if e.turn_id == run_id]
            if (exchange := _exchange(run)) is not None:
                exchanges.append(exchange)
                runs.extend(run)
        if not exchanges:
            return 0
        extracted = await extract_facts(await self._one_shot_for(session_id), exchanges)
        if not extracted.facts:
            return 0
        key = hashlib.sha256("\0".join(run_ids).encode()).hexdigest()[:32]
        return await scope.view.capture(
            binding.writable,
            extracted.facts,
            author=scope.audience.actor,
            context=CaptureContext(
                mode="automatic",
                actor=scope.audience.actor,
                source=scope.audience.target,
                session_id=session_id,
                sensitivity=extracted.sensitivity,
                # Everything these turns could have drawn on (QM captureDependencies).
                inherited=dependencies(runs),
                input="\n\n".join(x.input for x in exchanges),
                reply="\n\n".join(x.reply for x in exchanges),
                idempotency_key=key,
            ),
        )


def _checkpoint(event: object) -> list[dict[str, Any]] | None:
    """The conversation an event fixes, if it is a checkpoint."""
    if isinstance(event, RunEnd):
        return list(event.messages)
    if isinstance(event, CompactionEnd) and event.stop_reason == "ok" and event.messages is not None:
        return list(event.messages)
    return None


def _retained(events: Sequence[Envelope]) -> list[Shown] | None:
    """The last injection the model still holds, or None (inject everything).

    A compaction after it may have summarised it away, so then nothing is held.
    """
    for envelope in reversed(events):
        event = envelope.payload
        if isinstance(event, CompactionEnd) and event.stop_reason == "ok":
            return None
        if isinstance(event, MemoryInjected):
            return [Shown.from_dict(s) for s in event.shown]
    return None


def _exchange(run: Sequence[Envelope]) -> Exchange | None:
    """The user's message and the final reply of one completed run."""
    user = next(
        (e.payload for e in run if isinstance(e.payload, MessageEnd) and e.payload.role == "user"),
        None,
    )
    reply = next(
        (
            e.payload
            for e in reversed(run)
            if isinstance(e.payload, MessageEnd)
            and e.payload.role == "assistant"
            and not any(p.get("type") == "tool_call" for p in e.payload.content)
        ),
        None,
    )
    if user is None or reply is None:
        return None
    text, answer = message_text(user).strip(), message_text(reply).strip()
    return Exchange(text, answer) if text and answer else None
