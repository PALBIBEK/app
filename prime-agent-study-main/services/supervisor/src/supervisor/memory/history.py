"""A session's own history as short-term memory: search it, open one entry (QM history-search).

Only what the current audience may see is searched (events after the context
cutoff): messages and tool calls; tool results are excluded. Hits are clipped around the first match; an opened entry is capped
with its middle elided.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import UTC, datetime

from supervisor.agent.events import Envelope, MessageEnd, ToolStart

MAX_HIT_CHARS = 500
MATCH_LEAD_CHARS = 100
MAX_OPEN_CHARS = 20_000
OPEN_TAIL_CHARS = 2_000


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


def open_entry(events: Sequence[Envelope], seq: int) -> str | None:
    envelope = next((e for e in events if e.seq == seq), None)
    if envelope is None or (entry := _entry(envelope)) is None:
        return None
    kind, text = entry
    if len(text) > MAX_OPEN_CHARS:
        head = text[: MAX_OPEN_CHARS - OPEN_TAIL_CHARS]
        text = f"{head}\n… [{len(text)} chars total; middle elided] …\n{text[-OPEN_TAIL_CHARS:]}"
    return f"{_label(envelope, kind)} {text}"
