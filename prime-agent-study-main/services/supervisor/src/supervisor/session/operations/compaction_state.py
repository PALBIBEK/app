"""Compaction pairing helpers — fold journal events (harness-neutral).

Provides utilities for identifying unpaired compaction events
for recovery and crash detection.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from supervisor.agent.events import CompactionEnd, CompactionStart

if TYPE_CHECKING:
    from supervisor.agent.events import Envelope

__all__ = ["unpaired_compaction_starts"]


def unpaired_compaction_starts(envelopes: list[Envelope]) -> list[CompactionStart]:
    """Find CompactionStart payloads with no matching CompactionEnd.

    Useful for recovery to identify incomplete compaction operations.

    Args:
        envelopes: List of journal envelopes to process

    Returns:
        List of unpaired CompactionStart events
    """
    ended: set[str] = set()
    starts: dict[str, CompactionStart] = {}
    for env in envelopes:
        if isinstance(env.payload, CompactionStart):
            starts[env.payload.compaction_id] = env.payload
        elif isinstance(env.payload, CompactionEnd):
            ended.add(env.payload.compaction_id)
    return [starts[cid] for cid in starts if cid not in ended]
