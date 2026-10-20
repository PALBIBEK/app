"""In-memory session_id → live AgentRuntime for this process only.

A restart empties this and that is correct — reconstruct from the journal.
Never persist this map.
"""

from __future__ import annotations

import threading
from typing import Any


class SessionRegistry:
    def __init__(self) -> None:
        self._live: dict[str, Any] = {}
        self._lock = threading.Lock()

    def get(self, session_id: str) -> Any | None:
        with self._lock:
            return self._live.get(session_id)

    def put(self, session_id: str, runtime: Any) -> None:
        with self._lock:
            self._live[session_id] = runtime

    def remove(self, session_id: str) -> Any | None:
        with self._lock:
            return self._live.pop(session_id, None)

    def all(self) -> list[str]:
        with self._lock:
            return list(self._live.keys())
