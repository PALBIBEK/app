"""The audience checkpoint (QM context-boundary): history never crosses audiences.

Each run records a ``MemoryContext``: a fingerprint of who the session speaks to,
and a replay cutoff. If the audience changes (a scope member joins or leaves, the
session moves scope), the cutoff advances to the last event before the change,
and nothing at or before it — model history, recalled memory, tool results —
re-enters a later turn. An absent checkpoint is a baseline, not a deletion.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence

from supervisor.agent.events import Envelope, MemoryContext
from supervisor.memory.disclosure import Audience


def fingerprint(audience: Audience) -> str:
    body = {"target": audience.target, "members": sorted(set(audience.members))}
    return hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()


def latest_context(events: Sequence[Envelope]) -> MemoryContext | None:
    for envelope in reversed(events):
        if isinstance(envelope.payload, MemoryContext):
            return envelope.payload
    return None


def next_context(previous: MemoryContext | None, audience: str, latest_seq: int) -> MemoryContext:
    if previous is None:
        return MemoryContext(audience, -1)
    through = previous.through_seq if previous.audience == audience else latest_seq
    return MemoryContext(audience, through)


def cutoff(events: Sequence[Envelope]) -> int:
    context = latest_context(events)
    return -1 if context is None else context.through_seq


def bounded(events: Sequence[Envelope]) -> list[Envelope]:
    """The events a turn may see: after the cutoff, without the checkpoints themselves."""
    through = cutoff(events)
    return [e for e in events if e.seq > through and not isinstance(e.payload, MemoryContext)]
