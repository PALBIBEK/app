"""The consolidation pass — "dream" (port of Grok's `dream.rs`).

Periodically an auxiliary model reads recent session logs and rewrites
`MEMORY.md`: merging related facts, resolving contradictions, converting
relative dates to absolute, discarding ephemera. This is what turns a pile of
raw transcripts into curated knowledge.

Three safeguards do most of the work, and each exists because the obvious
implementation loses data:

1. **Only sessions actually read are deleted** (`processed_stems` vs the full
   eligible list). Input is capped, so sessions past the cap were never seen
   by the model; deleting them would destroy transcripts unread.
2. **Bad output is rejected before it is written.** A reply with no markdown
   headers is usually prose commentary, and writing it would overwrite
   curated memory with chat.
3. **The lock is always released.** Held after a crash, it silently stops
   consolidation forever.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Callable

from .dream_lock import DreamLock, sessions_since
from .storage import MemoryScope, MemoryStorage, is_scaffold_template
from .text_utils import has_markdown_headers, is_no_reply

MAX_DREAM_INPUT_CHARS = 32_000

DREAM_SYSTEM_PROMPT = """\
You are performing a dream — a reflective pass over memory files. Synthesize \
recent session logs into durable, well-organized memories so future sessions \
orient quickly.

You will receive the contents of recent session logs. You may also receive an \
existing memory document — merge it with new sessions rather than discarding \
prior knowledge. Your job:

1. **Merge** related information into coherent topic summaries
2. **Resolve** contradictions — if a recent session disproves an older fact, \
keep only the current truth
3. **Convert** relative dates ("yesterday", "last week") to absolute dates
4. **Discard** ephemeral details:
   - Greetings, meta-commentary, tool output noise
   - Message counts and tool-usage statistics
   - 'Current state' and 'Next steps' sections
   - Session metadata (dates, message counts)
5. **Preserve** decisions, rationale, architecture, preferences, and \
problem/solution pairs

Respond with a single markdown document. Use ## headers to separate topics. \
Each topic should be self-contained and useful to a future session that knows \
nothing about the current conversation.

If the session logs contain nothing worth persisting, respond with NO_REPLY."""


class DreamGate(str, Enum):
    OPEN = "open"
    DISABLED = "disabled"
    TOO_SOON = "too_soon"
    TOO_FEW_SESSIONS = "too_few_sessions"
    LOCKED = "locked"


class DreamStatus(str, Enum):
    SKIPPED = "skipped"
    COMPLETED = "completed"
    NOTHING_TO_CONSOLIDATE = "nothing_to_consolidate"
    FAILED = "failed"


@dataclass
class DreamConfig:
    enabled: bool = True
    min_hours: float = 6.0
    min_sessions: int = 3
    scope: MemoryScope = MemoryScope.WORKSPACE


@dataclass
class GateResult:
    status: DreamGate
    sessions: list[str] = field(default_factory=list)
    detail: str = ""


@dataclass
class DreamMessage:
    content: str
    processed_stems: list[str]
    """Only these were actually read. The caller deletes exactly these — never
    the full eligible list, or capped-out sessions vanish unread."""


@dataclass
class DreamResult:
    status: DreamStatus
    sessions_eligible: int = 0
    cleaned_stems: list[str] = field(default_factory=list)
    detail: str = ""


def check_dream_gates(config: DreamConfig, lock: DreamLock, sessions_dir: Path,
                      current_session_stem: str | None = None) -> GateResult:
    """Cheapest check first: config, then a stat, then a directory listing.

    Ordering is not cosmetic — this runs often, and checking `enabled` last
    would walk the filesystem on every turn for a feature that is switched
    off.
    """
    if not config.enabled:
        return GateResult(DreamGate.DISABLED)

    last = lock.last_consolidated_at() or 0.0
    if last:
        import time

        hours_since = (time.time() - last) / 3600.0
        if hours_since < config.min_hours:
            return GateResult(DreamGate.TOO_SOON,
                              detail=f"{hours_since:.1f}h since last consolidation")

    stems = sessions_since(sessions_dir, last)
    if current_session_stem:
        # The in-flight run is still being appended to; consolidating and
        # deleting it would pull the transcript out from under the caller.
        stems = [s for s in stems if s != current_session_stem]

    if len(stems) < config.min_sessions:
        return GateResult(DreamGate.TOO_FEW_SESSIONS, sessions=stems,
                          detail=f"{len(stems)} of {config.min_sessions} required")

    return GateResult(DreamGate.OPEN, sessions=stems)


def build_dream_message(storage: MemoryStorage, stems: list[str],
                        scope: MemoryScope = MemoryScope.WORKSPACE) -> DreamMessage:
    """Assemble the prompt, stopping at the input cap.

    Existing memory is prepended so the model *merges* rather than replaces —
    without it, every consolidation silently discards everything learned
    before. Scaffold boilerplate is excluded: passing the stub as "prior
    knowledge" invites the model to preserve template text as though it were
    a fact.
    """
    parts: list[str] = []
    budget = MAX_DREAM_INPUT_CHARS

    existing = storage.read(scope)
    if existing.strip() and not is_scaffold_template(existing):
        block = f"# Existing memory (merge with the sessions below)\n\n{existing}\n"
        parts.append(block)
        budget -= len(block)

    processed: list[str] = []
    by_stem = {p.stem: p for p in storage.list_sessions()}

    for stem in stems:
        path = by_stem.get(stem)
        if path is None:
            continue
        text = storage.read_session(path)
        if not text.strip():
            processed.append(stem)          # empty file: nothing to read, safe to drop
            continue
        block = f"\n# Session: {stem}\n\n{text}\n"
        if len(block) > budget and processed:
            break                            # over budget: leave the rest for next time
        parts.append(block)
        processed.append(stem)
        budget -= len(block)

    return DreamMessage(content="".join(parts), processed_stems=processed)


def process_dream_response(response: str) -> str | None:
    """Validate the model's reply, returning the document or None.

    Two rejections, both preventing a bad write to the only copy of curated
    knowledge: `NO_REPLY` (the model's way of saying nothing was worth
    keeping) and header-free prose (usually commentary such as "Sure! Here's
    what I found", which would overwrite memory with chat).
    """
    trimmed = (response or "").strip()
    if not trimmed:
        return None
    if is_no_reply(trimmed):
        return None
    if not has_markdown_headers(trimmed):
        return None
    return trimmed


def run_dream(storage: MemoryStorage, config: DreamConfig,
              complete: Callable[[str, str], str],
              lock_dir: Path | None = None,
              current_session_stem: str | None = None) -> DreamResult:
    """Run one consolidation if the gates allow it.

    `complete(system_prompt, user_message) -> str` is injected rather than a
    client being constructed here, so this module never imports an LLM
    client and the whole pass is testable offline.
    """
    lock = DreamLock(Path(lock_dir) if lock_dir else storage.workspace_dir())

    gate = check_dream_gates(config, lock, storage.sessions_dir(), current_session_stem)
    if gate.status is not DreamGate.OPEN:
        return DreamResult(DreamStatus.SKIPPED, detail=gate.detail or gate.status.value)

    if not lock.try_acquire():
        return DreamResult(DreamStatus.SKIPPED, detail="another dream is running")

    try:
        message = build_dream_message(storage, gate.sessions, config.scope)
        if not message.content.strip():
            return DreamResult(DreamStatus.NOTHING_TO_CONSOLIDATE,
                               sessions_eligible=len(gate.sessions))

        try:
            reply = complete(DREAM_SYSTEM_PROMPT, message.content)
        except Exception as exc:  # noqa: BLE001 - a model outage must not lose logs
            return DreamResult(DreamStatus.FAILED, sessions_eligible=len(gate.sessions),
                               detail=f"{type(exc).__name__}: {exc}")

        document = process_dream_response(reply)
        if document is None:
            # Sessions are deliberately kept: "nothing worth persisting" is
            # not a licence to destroy the evidence.
            return DreamResult(DreamStatus.NOTHING_TO_CONSOLIDATE,
                               sessions_eligible=len(gate.sessions))

        storage.write(config.scope, document + "\n")

        cleaned: list[str] = []
        by_stem = {p.stem: p for p in storage.list_sessions()}
        for stem in message.processed_stems:      # never gate.sessions
            path = by_stem.get(stem)
            if path is not None:
                storage.delete_session(path)
                cleaned.append(stem)

        lock.mark_consolidated()
        return DreamResult(DreamStatus.COMPLETED, sessions_eligible=len(gate.sessions),
                           cleaned_stems=cleaned)
    finally:
        # Unconditionally. A lock held past a crash silently disables
        # consolidation forever and nothing reports it. `release()` preserves
        # the mtime, so calling it after `mark_consolidated()` is harmless.
        lock.release()
