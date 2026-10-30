"""What the gateway answered and spent, as Prometheus text (``GET /v1/metrics``).

Every model call of the platform passes through the gateway, so its spend counter is the
platform's: ``deploy/alerts/platform.rules.yml`` alerts on an hour's increase, and on the
share of calls the upstream providers fail. Counts only: no tenant, session or key.

The supervisor API counts its responses the same way (``supervisor.obs.responses``); the
gateway keeps its own counter, as it imports nothing from the rest of the supervisor.
"""

from __future__ import annotations

import threading
from collections import Counter
from typing import Final

from starlette.types import ASGIApp, Message, Receive, Scope, Send

__all__ = ["INFERENCE_PATHS", "CountModelCalls", "ModelCalls", "Spend", "prometheus"]

INFERENCE_PATHS: Final = frozenset({"/v1/chat/completions", "/v1/messages", "/v1/responses"})
"""The paths whose responses are counted: the model calls."""


class ModelCalls:
    """Model calls answered since the process started, by status code (handlers run in a
    thread pool, hence the lock)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._by_code: Counter[int] = Counter()

    def add(self, code: int) -> None:
        with self._lock:
            self._by_code[code] += 1

    def snapshot(self) -> dict[int, int]:
        with self._lock:
            return dict(sorted(self._by_code.items()))


class CountModelCalls:
    """ASGI middleware: count each response on ``INFERENCE_PATHS``; one that raised is the
    500 the server then sends."""

    def __init__(self, app: ASGIApp, *, calls: ModelCalls) -> None:
        self._app = app
        self._calls = calls

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope.get("path") not in INFERENCE_PATHS:
            await self._app(scope, receive, send)
            return
        started = False

        async def counting(message: Message) -> None:
            nonlocal started
            if message["type"] == "http.response.start" and not started:
                started = True
                self._calls.add(int(message["status"]))
            await send(message)

        try:
            await self._app(scope, receive, counting)
        except Exception:
            if not started:
                self._calls.add(500)
            raise


class Spend:
    """US dollars charged since the process started."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._usd = 0.0

    def add(self, usd: float) -> None:
        with self._lock:
            self._usd += usd

    @property
    def usd(self) -> float:
        with self._lock:
            return self._usd


def prometheus(calls: ModelCalls, spend: Spend) -> str:
    lines = [
        "# HELP yal_gateway_responses_total Model calls the gateway answered, by status code.",
        "# TYPE yal_gateway_responses_total counter",
        *(f'yal_gateway_responses_total{{code="{code}"}} {count}' for code, count in calls.snapshot().items()),
        "# HELP yal_gateway_spend_usd_total Model spend the gateway charged, in US dollars.",
        "# TYPE yal_gateway_spend_usd_total counter",
        f"yal_gateway_spend_usd_total {spend.usd:.6f}",
    ]
    return "\n".join(lines) + "\n"
