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
    MemoryContext,
    MemoryInjected,
    MessageEnd,
    RunEnd,
    ToolStart,
)
from supervisor.journal.reader import JournalReader
from supervisor.journal.serde import type_name_of
from supervisor.journal.writer import keyed_event_id
from supervisor.memory import boundary
from supervisor.memory.binding import CaptureMode, MemoryBinding, Strategy
from supervisor.memory.classification import OneShot
from supervisor.memory.client import MemoryRefused, MemoryRequestInvalid, MemoryUnavailable
from supervisor.memory.cross import recall_across
from supervisor.memory.delta import Shown, held_hashes, recall_delta
from supervisor.memory.disclosure import Audience
from supervisor.memory.extraction import Exchange, extract_facts
from supervisor.memory.history import message_text
from supervisor.memory.platform import MemoryPlatform
from supervisor.memory.resolution import resolve
from supervisor.memory.service import CaptureContext, MemoryService
from supervisor.memory.tools import PLATFORM_TOOLS, SessionsTool, TurnMemory
from supervisor.memory_service.notebooks.errors import (
    MemoryDisclosureDenied,
    MemoryFactsMissing,
    NotebookArchived,
)
from supervisor.memory_service.notebooks.types import (
    UNTRACKED,
    Provenance,
    RecallContext,
    Sensitivity,
    personal_source,
)

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


RECALL_UNAVAILABLE = (
    "[Long-term memory is unavailable right now, so nothing was recalled for this turn. "
    "Do not conclude the user has no saved facts; say so if it matters.]"
)


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
        """Every event the session's audience may see: its whole journal past the cutoff.
        For what reads all of it (the history tool, a compaction naming what it replaced);
        what needs a few events reads only those (``recent_messages``, ``_retained_now``)."""
        context = await self._latest_context(session_id)
        through = -1 if context is None else context.through_seq
        events = await self._reader.read(session_id, since=through)
        return [e for e in events if not isinstance(e.payload, MemoryContext)]

    async def recent_messages(
        self, session_id: str, enough: Callable[[Sequence[Envelope]], bool], *, page: int = 16
    ) -> list[Envelope]:
        """The session's latest messages past the cutoff, oldest first: pages of them read
        newest first until ``enough`` says these suffice, or there are no more."""
        context = await self._latest_context(session_id)
        through = -1 if context is None else context.through_seq
        found: list[Envelope] = []
        before: int | None = None
        while True:
            batch = await self._reader.last_of(session_id, _MESSAGES, limit=page, before_seq=before)
            fresh = [e for e in batch if e.seq > through]
            found = [*reversed(fresh), *found]
            if len(fresh) < page or enough(found):
                return found
            before = batch[-1].seq

    async def _retained_now(self, session_id: str) -> list[Shown] | None:
        """The last injection the model still holds, or None (inject everything): read
        from the latest injections and compactions past the cutoff. A compaction after it
        may have summarised it away, so then nothing is held."""
        context = await self._latest_context(session_id)
        through = -1 if context is None else context.through_seq
        before: int | None = None
        while True:
            page = await self._reader.last_of(session_id, _HELD, limit=8, before_seq=before)
            for envelope in page:
                if envelope.seq <= through:
                    return None
                event = envelope.payload
                if isinstance(event, CompactionEnd) and event.stop_reason == "ok":
                    return None
                if isinstance(event, MemoryInjected):
                    return [Shown.from_dict(s) for s in event.shown]
            if len(page) < 8:
                return None
            before = page[-1].seq

    # --- checkpoint --------------------------------------------------------------

    async def checkpoint(self, session_id: str, run_id: str, record: Record) -> None:
        """Journal this run's audience checkpoint (advancing the cutoff if it changed)."""
        scope = await self.scope(session_id)
        if scope is None:
            return
        previous = await self._latest_context(session_id)
        nxt = boundary.next_context(
            previous, boundary.fingerprint(scope.audience), await self._reader.last_seq(session_id)
        )
        if previous is None or nxt != previous:
            await record(session_id, run_id, "memory_context", nxt)

    async def prior_history(self, session_id: str) -> list[dict[str, Any]]:
        """The conversation carried into the next run, unless the audience cutoff has passed it.

        The latest checkpoint wins: a run's closing messages, or the compacted
        conversation a later compaction replaced them with. Read from the latest few
        events of those types, not the whole journal (this runs on every model step).
        """
        context = await self._latest_context(session_id)
        through = -1 if context is None else context.through_seq
        before: int | None = None
        while True:
            page = await self._reader.last_of(session_id, _CHECKPOINTS, limit=8, before_seq=before)
            for event in page:
                if event.seq <= through:
                    return []
                if (messages := _checkpoint(event.payload)) is not None:
                    return messages
            if len(page) < 8:
                return []
            before = page[-1].seq

    async def _latest_context(self, session_id: str) -> MemoryContext | None:
        latest = await self._reader.last_of(session_id, [type_name_of(MemoryContext)])
        return latest[0].payload if latest and isinstance(latest[0].payload, MemoryContext) else None

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
            assert isinstance(existing.payload, MemoryInjected), "the memory key holds recalls"
            return existing.payload
        scope = await self.scope(session_id)
        if scope is None or not scope.binding.recall_notebooks:
            return None
        recent = await self.recent_messages(session_id, _four_exchanges)
        labels = scope.binding.labels
        try:
            recalled = await recall_across(
                scope.view,
                scope.binding.recall_notebooks,
                RecallContext(query=recall_query(message, recent), actor=scope.audience.actor),
                labels,
            )
            held = {
                nb: held_hashes(await scope.view.facts(nb))
                for nb in scope.binding.recall_notebooks
            }
        except (MemoryUnavailable, MemoryRefused, MemoryRequestInvalid) as exc:
            # Long-term memory is an aid: an outage costs this turn its recall, never the turn.
            logger.warning("session %s: recall skipped, memory unavailable: %s", session_id, exc)
            skipped = MemoryInjected(run_id, RECALL_UNAVAILABLE, [])
            await record(session_id, run_id, "memory", skipped)
            return skipped
        delta = recall_delta(recalled, await self._retained_now(session_id), held, labels)
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
        """What the session's tool calls need; its journal is read only by a call that uses
        it (every step lists the schemas, few call ``history`` or ``memory``)."""
        scope = await self.scope(session_id)

        async def load() -> tuple[Sequence[Envelope], tuple[Provenance, ...]]:
            events = await self.bounded(session_id)
            return events, dependencies(events)

        if scope is None:
            return TurnMemory(None, None, None, session_id, load=load)
        return TurnMemory(
            scope.view,
            scope.binding,
            scope.audience,
            session_id,
            load=load,
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
        context = await self._latest_context(session_id)
        through = -1 if context is None else context.through_seq
        exchanges, runs = [], []
        for run_id in run_ids:
            run = [e for e in await self._reader.read_turn(session_id, run_id) if e.seq > through]
            if (exchange := _exchange(run)) is not None:
                exchanges.append(exchange)
                runs.extend(run)
        if not exchanges:
            return 0
        one_shot = await self._one_shot_for(session_id)
        extracted = await extract_facts(one_shot, exchanges)
        if not extracted.facts:
            return 0
        # Resolve against what the notebook holds (supervisor.memory.resolution): a
        # duplicate is not added; a fact the new ones make false is retired after them.
        resolution = await resolve(one_shot, extracted.facts, await scope.view.facts(binding.writable))
        facts = [extracted.facts[i] for i in resolution.add]
        key = hashlib.sha256("\0".join(run_ids).encode()).hexdigest()[:32]
        added = 0
        if facts:
            added = await self._capture_facts(
                scope, binding.writable, session_id, facts, extracted.sensitivity, runs, exchanges, key
            )
        await self._retire(scope, binding.writable, session_id, resolution.retire)
        return added

    async def _retire(self, scope: Scope, notebook: str, session_id: str, ids: Sequence[str]) -> None:
        """Delete facts a capture made false (history keeps them); best effort, after the
        capture, so a failure leaves both facts rather than neither."""
        if not ids:
            return
        try:
            await scope.view.delete(notebook, list(ids), author=scope.audience.actor)
        except (MemoryFactsMissing, MemoryDisclosureDenied, NotebookArchived) as exc:
            # Already gone (a retried capture), not this audience's to change, or archived.
            logger.info("session %s: contradicted facts not retired: %s", session_id, exc)

    async def _capture_facts(
        self,
        scope: Scope,
        notebook: str,
        session_id: str,
        facts: Sequence[str],
        sensitivity: Sensitivity,
        runs: Sequence[Envelope],
        exchanges: Sequence[Exchange],
        key: str,
    ) -> int:
        return await scope.view.capture(
            notebook,
            list(facts),
            author=scope.audience.actor,
            context=CaptureContext(
                mode="automatic",
                actor=scope.audience.actor,
                source=scope.audience.target,
                session_id=session_id,
                sensitivity=sensitivity,
                # Everything these turns could have drawn on (QM captureDependencies).
                inherited=dependencies(runs),
                input="\n\n".join(x.input for x in exchanges),
                reply="\n\n".join(x.reply for x in exchanges),
                idempotency_key=key,
            ),
        )


_CHECKPOINTS: Final = (type_name_of(RunEnd), type_name_of(CompactionEnd))
_MESSAGES: Final = (type_name_of(MessageEnd),)
_HELD: Final = (type_name_of(MemoryInjected), type_name_of(CompactionEnd))


def _four_exchanges(messages: Sequence[Envelope]) -> bool:
    """Enough for ``recall_query``: its last four exchanges' text."""
    said = [e for e in messages if isinstance(e.payload, MessageEnd) and e.payload.role in ("user", "assistant")]
    return len(said) >= 4


def _checkpoint(event: object) -> list[dict[str, Any]] | None:
    """The conversation an event fixes, if it is a checkpoint."""
    if isinstance(event, RunEnd):
        return list(event.messages)
    if isinstance(event, CompactionEnd) and event.stop_reason == "ok" and event.messages is not None:
        return list(event.messages)
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
