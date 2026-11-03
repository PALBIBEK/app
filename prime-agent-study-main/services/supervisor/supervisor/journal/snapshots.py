"""How a run's closing conversation is stored: what it added, not all of it again.

Every ``RunEnd`` carries the whole conversation so far, so a session of N runs stored the
conversation N times over (quadratic in its length). Most runs only append to the previous
run's conversation, so a ``RunEnd`` whose messages extend the session's previous ``RunEnd``
is stored as a delta: the previous one's seq (its base), how many of its messages are kept,
and the messages after them. A conversation that is not an extension (a compaction rewrote
it), or a chain already ``MAX_CHAIN`` deltas deep, is stored in full: reading one never
needs more than ``MAX_CHAIN`` earlier rows, all of them ``RunEnd`` rows of the same session.

The stored type is ``RunEndDelta`` so a build from before deltas, which knows only
``RunEnd``, reads one as an event it does not know rather than as a shorter conversation.
The reader resolves deltas back into ``RunEnd`` events; nothing else sees them.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Final

__all__ = ["DELTA_TYPE", "MAX_CHAIN", "Snapshot", "depth", "encode_run_end", "is_delta", "resolve"]

DELTA_TYPE: Final = "RunEndDelta"
MAX_CHAIN: Final = 16
"""The most deltas between a full ``RunEnd`` and any delta built on it."""


@dataclass(frozen=True)
class Snapshot:
    """A stored ``RunEnd``'s conversation, resolved: where it is and how deep its chain is."""

    seq: int
    depth: int  # 0 when stored in full
    messages: list[dict[str, Any]]


def is_delta(payload: dict[str, Any]) -> bool:
    return payload.get("_type") == DELTA_TYPE


def encode_run_end(payload: dict[str, Any], base: Snapshot | None) -> dict[str, Any]:
    """``payload`` (an encoded ``RunEnd``) as stored after ``base``, the session's previous one."""
    messages: list[dict[str, Any]] = payload["messages"]
    if base is None or base.depth >= MAX_CHAIN:
        return payload
    keep = _common_prefix(base.messages, messages)
    if keep == 0:
        return payload
    rest = {k: v for k, v in payload.items() if k not in ("_type", "messages")}
    return {
        "_type": DELTA_TYPE,
        **rest,
        "_base_seq": base.seq,
        "_keep": keep,
        "_depth": base.depth + 1,
        "messages": messages[keep:],
    }


def resolve(payload: dict[str, Any], base: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """A delta ``payload`` as the ``RunEnd`` it stands for, given its base's messages."""
    keep = int(payload["_keep"])
    if keep > len(base):
        raise ValueError(f"delta keeps {keep} messages of a base with {len(base)}")
    rest = {k: v for k, v in payload.items() if k not in ("_type", "_base_seq", "_keep", "_depth", "messages")}
    return {"_type": "RunEnd", **rest, "messages": [*base[:keep], *payload["messages"]]}


def depth(payload: dict[str, Any]) -> int:
    return int(payload.get("_depth", 0))


def _common_prefix(old: Sequence[dict[str, Any]], new: Sequence[dict[str, Any]]) -> int:
    n = 0
    for a, b in zip(old, new, strict=False):
        if a != b:
            break
        n += 1
    return n
