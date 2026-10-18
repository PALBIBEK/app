"""Compaction pairing helpers — fold journal events (harness-neutral)."""

from __future__ import annotations

from supervisor.agent.events import CompactionEnd, CompactionStart, Envelope


def unpaired_compaction_starts(envelopes: list[Envelope]) -> list[CompactionStart]:
    """CompactionStart payloads whose compaction_id has no matching CompactionEnd."""
    ended: set[str] = set()
    starts: dict[str, CompactionStart] = {}
    for env in envelopes:
        if isinstance(env.payload, CompactionStart):
            starts[env.payload.compaction_id] = env.payload
        elif isinstance(env.payload, CompactionEnd):
            ended.add(env.payload.compaction_id)
    return [starts[cid] for cid in starts if cid not in ended]
