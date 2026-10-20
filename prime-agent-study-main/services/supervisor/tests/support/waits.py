"""Wait for what a test needs to be true, never for a fixed time.

A fixed sleep is either too short on a slow runner (a flaky failure) or longer
than needed everywhere (a slow suite). These poll a condition with a deadline.

To show that something did *not* happen, wait for a later event that could only
come after it would have (a barrier), then assert it is absent.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from supervisor.agent.events import Envelope

TIMEOUT_S = 15.0


async def until[T](
    probe: Callable[[], Awaitable[T]],
    done: Callable[[T], bool] = bool,
    *,
    timeout: float = TIMEOUT_S,
    every: float = 0.02,
) -> T:
    """Poll ``probe`` until ``done`` holds for its value; return that value."""
    async with asyncio.timeout(timeout):
        while not done(value := await probe()):
            await asyncio.sleep(every)
    return value


def counted(name: str, n: int = 1) -> Callable[[list[Envelope]], bool]:
    """Journal holds at least ``n`` events of type ``name``."""
    return lambda events: [type(e.payload).__name__ for e in events].count(name) >= n


async def journalled(
    reader: Any, session_id: str, done: Callable[[list[Envelope]], bool], **kw: Any
) -> list[Envelope]:
    """The session's journal once ``done`` holds for it."""
    return await until(lambda: reader.read(session_id, since=-1), done, **kw)


async def payload_names(
    manager: Any, session_id: str, done: Callable[[list[str]], bool], **kw: Any
) -> list[str]:
    """The manager's event type names for a session once ``done`` holds for them."""
    return await until(lambda: manager.event_payloads(session_id), done, **kw)


async def settled(sessions: Any, session_id: str, turns: int, **kw: Any) -> Any:
    """A session workflow that finished ``turns`` turns with nothing queued or running.

    Once settled, a command it accepted as a duplicate can no longer start a run:
    duplicates are never queued, so there is nothing left to wait for.
    """

    def idle(status: Any) -> bool:
        return (
            status.turns_completed >= turns
            and status.queued == 0
            and status.running_command is None
        )

    return await until(lambda: sessions.status(session_id), idle, **kw)
