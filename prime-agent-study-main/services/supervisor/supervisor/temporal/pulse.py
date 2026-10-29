"""Every activity heartbeats while it runs, so a dead worker is found in seconds.

Temporal notices a worker that died mid-step only through the step's heartbeat timeout,
or else its whole time limit. Before, only long steps heartbeated: a worker killed during a
short step on the session's worker (closing a run, preparing one, an approval) held the
session for that step's whole limit (2 minutes). pi-temporal gives every step a 30 s
heartbeat timeout; this interceptor makes every activity heartbeat, so every step can have
one (supervisor.temporal.policies).
"""

from __future__ import annotations

import asyncio
import contextlib
from datetime import timedelta
from typing import Any, Final

from temporalio import activity
from temporalio.worker import ActivityInboundInterceptor, ExecuteActivityInput, Interceptor

EVERY: Final = timedelta(seconds=2)
"""At most this long between heartbeats (a third of a shorter timeout)."""


class Pulse(Interceptor):
    """Heartbeat every activity that has a heartbeat timeout, while it runs."""

    def intercept_activity(self, next: ActivityInboundInterceptor) -> ActivityInboundInterceptor:
        return _Beating(next)


class _Beating(ActivityInboundInterceptor):
    async def execute_activity(self, input: ExecuteActivityInput) -> Any:
        timeout = activity.info().heartbeat_timeout
        if not timeout:
            return await super().execute_activity(input)
        every = min(timeout.total_seconds() / 3, EVERY.total_seconds())

        async def beat() -> None:
            while True:
                await asyncio.sleep(every)
                activity.heartbeat()

        pulse = asyncio.create_task(beat())  # in the activity's context, so it may heartbeat
        try:
            return await super().execute_activity(input)
        finally:
            pulse.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await pulse
