"""A bounded, per-process record of recent model calls (QM's model-gateway audit).

The journal is the durable record of every call a session makes; this is the
operator's quick view across sessions of what was sent where, and how big it was.
"""

from __future__ import annotations

import threading
from collections import deque
from dataclasses import dataclass

DEFAULT_MAX_RECORDS = 1_000


@dataclass(frozen=True, slots=True)
class ModelCallRecord:
    at: float
    """Unix seconds."""
    scope: str
    """Who made the call: a session id."""
    model: str
    input_tokens: int
    entry_count: int
    """Messages sent."""


class ModelCallAudit:
    def __init__(self, max_records: int = DEFAULT_MAX_RECORDS) -> None:
        self._records: deque[ModelCallRecord] = deque(maxlen=max(1, max_records))
        self._lock = threading.Lock()

    def record(self, record: ModelCallRecord) -> None:
        with self._lock:
            self._records.append(record)

    def records(self) -> tuple[ModelCallRecord, ...]:
        with self._lock:
            return tuple(self._records)
