"""A session's own history as short-term memory: search it, read slices of it, open entries.

The journal is the session (the brain–hands split's ``getEvents``): the model
reads positional slices of it, not only what its context window still holds, so
nothing compaction removed is ever lost to it.

* **search** (QM history-search): messages and tool calls, newest first; tool
  results are left out, so a search is about what was said and done;
* **open** one entry by its seq, tool results included, verbatim up to a cap
  (the middle of a longer one is elided);
* **slice** a seq range, or one tool call with its result, bounded in size.

Only what the current audience may see is read (events after the context cutoff).
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import UTC, datetime

from supervisor.agent.events import Envelope, MessageEnd, ToolEnd, ToolStart

MAX_HIT_CHARS = 500
MATCH_LEAD_CHARS = 100
MAX_OPEN_CHARS = 20_000
OPEN_TAIL_CHARS = 2_000
MAX_SLICE_ENTRIES = 50
MAX_SLICE_CHARS = 20_000


def message_text(event: MessageEnd) -> str:
    return "".join(
        str(part.get("text", ""))
        for part in event.content
        if isinstance(part, dict) and part.get("type") == "text"
    )


def _entry(envelope: Envelope) -> tuple[str, str] | None:
    """(kind, text) for entries worth searching; None for bookkeeping events."""
    event = envelope.payload
    if isinstance(event, MessageEnd) and event.role in ("user", "assistant"):
        text = message_text(event).strip()
        return (event.role, text) if text else None
    if isinstance(event, ToolStart):  # calls, not results (QM: results are excluded)
        return ("tool_call", f"{event.tool_name} {json.dumps(event.args, default=str)}")
    return None


def _result(envelope: Envelope) -> tuple[str, str] | None:
    """A tool result as an entry (opened and sliced, never searched)."""
    event = envelope.payload
    if not isinstance(event, ToolEnd):
        return None
    kind = "tool_error" if event.is_error else "tool_result"
    payload = event.result.get("payload")
    if isinstance(payload, str) and not event.is_error:
        body = payload  # a text result (a file, a command's output) as the tool returned it
    else:
        body = json.dumps(event.result, default=str)
    return kind, f"{event.tool_name} [{event.tool_call_id}]\n{body}"


def _any_entry(envelope: Envelope) -> tuple[str, str] | None:
    return _entry(envelope) or _result(envelope)


def _label(envelope: Envelope, kind: str) -> str:
    stamp = datetime.fromtimestamp(envelope.ts_ms / 1000, tz=UTC).isoformat()
    return f"{kind}#{envelope.seq} ({stamp}):"


def _clip(text: str, terms: Sequence[str]) -> str:
    if len(text) <= MAX_HIT_CHARS:
        return text
    lower = text.lower()
    positions = [p for p in (lower.find(t) for t in terms) if p >= 0]
    first = min(positions) if positions else 0
    if first + MATCH_LEAD_CHARS <= MAX_HIT_CHARS:
        return f"{text[:MAX_HIT_CHARS]}…"
    start = first - MATCH_LEAD_CHARS
    end = start + MAX_HIT_CHARS
    return f"…{text[start:end]}{'…' if end < len(text) else ''}"


def search(events: Sequence[Envelope], query: str, limit: int = 20) -> list[str]:
    """Newest first; every term must appear (case-insensitive)."""
    terms = [t for t in query.lower().split() if t]
    if not terms:
        return []
    hits: list[str] = []
    for envelope in reversed(events):
        if len(hits) >= limit:
            break
        if (entry := _entry(envelope)) is None:
            continue
        kind, text = entry
        if all(t in text.lower() for t in terms):
            hits.append(f"{_label(envelope, kind)} {_clip(text, terms)}")
    return hits


def _capped(text: str, cap: int = MAX_OPEN_CHARS) -> str:
    if len(text) <= cap:
        return text
    head = text[: cap - OPEN_TAIL_CHARS]
    return f"{head}\n… [{len(text)} chars total; middle elided] …\n{text[-OPEN_TAIL_CHARS:]}"


def open_entry(events: Sequence[Envelope], seq: int) -> str | None:
    """One entry in full (a tool result too), capped with its middle elided."""
    envelope = next((e for e in events if e.seq == seq), None)
    if envelope is None or (entry := _any_entry(envelope)) is None:
        return None
    kind, text = entry
    return f"{_label(envelope, kind)} {_capped(text)}"


def slice_entries(events: Sequence[Envelope], from_seq: int, to_seq: int | None = None) -> str:
    """The entries from ``from_seq`` to ``to_seq`` (inclusive), oldest first, bounded.

    Each entry is clipped to a hit's length; a slice that stops early says where
    to carry on. Open one entry by its seq for the whole of it.
    """
    shown: list[str] = []
    used = 0
    for envelope in events:
        if envelope.seq < from_seq or (to_seq is not None and envelope.seq > to_seq):
            continue
        if (entry := _any_entry(envelope)) is None:
            continue
        kind, text = entry
        clipped = text if len(text) <= MAX_HIT_CHARS else f"{text[:MAX_HIT_CHARS]}… (open #{envelope.seq})"
        line = f"{_label(envelope, kind)} {clipped}"
        if len(shown) >= MAX_SLICE_ENTRIES or used + len(line) > MAX_SLICE_CHARS:
            shown.append(f"[more entries follow: continue with from_seq={envelope.seq}]")
            break
        shown.append(line)
        used += len(line)
    return "\n".join(shown)


def call_entries(events: Sequence[Envelope], call_id: str) -> str | None:
    """One tool call and its result, both in full (capped)."""
    found = [
        f"{_label(e, kind)} {_capped(text)}"
        for e in events
        if isinstance(e.payload, (ToolStart, ToolEnd))
        and e.payload.tool_call_id == call_id
        and (entry := _any_entry(e)) is not None
        for kind, text in (entry,)
    ]
    return "\n".join(found) or None
