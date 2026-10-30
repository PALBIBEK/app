"""The supervisor API's responses counted by status code, per process (``GET /v1/ops/metrics``).

An alert watches the API's error rate with them (``deploy/alerts/platform.rules.yml``).
Counts only: no path, tenant or caller is recorded, so the scrape names nothing. A request
that raised counts as the 500 the server then sends. (The model gateway counts its calls
the same way in ``supervisor.gateway.metrics``.)
"""

from __future__ import annotations

import threading
from collections import Counter
from collections.abc import Iterable

from starlette.types import ASGIApp, Message, Receive, Scope, Send

__all__ = ["CountResponses", "ResponseCounts", "counter_lines"]


class ResponseCounts:
    """Responses by status code since the process started (safe across threads: sync
    handlers run in a thread pool)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._by_code: Counter[int] = Counter()

    def add(self, code: int) -> None:
        with self._lock:
            self._by_code[code] += 1

    def snapshot(self) -> dict[int, int]:
        with self._lock:
            return dict(sorted(self._by_code.items()))


class CountResponses:
    """ASGI middleware: count each HTTP response."""

    def __init__(self, app: ASGIApp, *, counts: ResponseCounts) -> None:
        self._app = app
        self._counts = counts

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return
        started = False

        async def counting(message: Message) -> None:
            nonlocal started
            if message["type"] == "http.response.start" and not started:
                started = True
                self._counts.add(int(message["status"]))
            await send(message)

        try:
            await self._app(scope, receive, counting)
        except Exception:
            if not started:
                self._counts.add(500)
            raise


def counter_lines(name: str, help_text: str, counts: dict[int, int]) -> Iterable[str]:
    """``counts`` as a Prometheus counter labelled by ``code``."""
    yield f"# HELP {name} {help_text}"
    yield f"# TYPE {name} counter"
    for code, count in counts.items():
        yield f'{name}{{code="{code}"}} {count}'
