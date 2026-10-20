"""Time to first token, read from the journal.

TTFT here is the time from a run's ``RunStart`` to its first assistant
``MessageEnd``: what the user waits for once their message starts running
(queueing behind an earlier message is not counted). Both events are journalled
with their timestamps, so the measure is the same whichever process recorded
them, and needs no counter that only one worker would see.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from supervisor.db.models import EventRow

__all__ = ["Ttft", "recent_ttft", "ttfts"]

_TYPES = ("RunStart", "MessageEnd")


@dataclass(frozen=True, slots=True)
class Ttft:
    runs: int
    p50_ms: int | None
    p95_ms: int | None

    def to_dict(self) -> dict[str, int | None]:
        return {"runs": self.runs, "p50_ms": self.p50_ms, "p95_ms": self.p95_ms}


def ttfts(rows: Iterable[tuple[str, str | None, str, dict[str, Any], int]]) -> list[int]:
    """Per run: first assistant answer minus its start, in ms.

    ``rows``: (session id, run id, event type, payload, ts_ms) in journal order.
    """
    started: dict[tuple[str, str], int] = {}
    answered: dict[tuple[str, str], int] = {}
    for session_id, run_id, kind, payload, ts_ms in rows:
        if run_id is None:
            continue
        key = (session_id, run_id)
        if kind == "RunStart":
            started.setdefault(key, ts_ms)
        elif kind == "MessageEnd" and payload.get("role") == "assistant":
            answered.setdefault(key, ts_ms)
    return [
        answered[k] - started[k] for k in answered if k in started and answered[k] >= started[k]
    ]


def _percentile(values: Sequence[int], q: float) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(q * len(ordered)) - 1)]


async def recent_ttft(db: AsyncSession, *, events: int = 5000) -> Ttft:
    """TTFT percentiles over the runs in the most recent ``events`` start/answer events."""
    recent = (
        select(
            EventRow.session_id, EventRow.turn_id, EventRow.type, EventRow.payload, EventRow.ts_ms
        )
        .where(EventRow.type.in_(_TYPES))
        .order_by(EventRow.ts_ms.desc())
        .limit(events)
    )
    rows = sorted((await db.execute(recent)).all(), key=lambda r: (r[0], r[4]))
    values = ttfts(rows)
    return Ttft(len(values), _percentile(values, 0.5), _percentile(values, 0.95))
