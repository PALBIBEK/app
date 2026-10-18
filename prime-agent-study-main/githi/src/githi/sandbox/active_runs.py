"""Track cancellable bash tool runs separately from the agent interrupt."""

from __future__ import annotations

import threading


class BashRunRegistry:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._runs: set[threading.Event] = set()

    def register(self) -> threading.Event:
        signal = threading.Event()
        with self._lock:
            self._runs.add(signal)
        return signal

    def unregister(self, signal: threading.Event) -> None:
        with self._lock:
            self._runs.discard(signal)

    def abort_all(self) -> bool:
        with self._lock:
            runs = list(self._runs)
        for signal in runs:
            signal.set()
        return bool(runs)
