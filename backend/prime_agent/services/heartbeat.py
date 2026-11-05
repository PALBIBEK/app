"""Heartbeats for background work: proof that a run is still alive, whoever reads it.

Experiments and proposals run as background tasks of an API process. When the process
stops (a deploy, a crash), the task goes with it and its row would read "running" forever.
So while the work runs, a beat task stamps the row's ``heartbeat_at`` every ``HEARTBEAT_S``;
a reader that finds active work with no beat for ``STALLED_AFTER`` knows it was cut off and
settles it as failed. A beat is independent of the work's own steps, so a step that takes
long (a replay waiting minutes for a model) never looks stalled.

This is procrastinate's worker heartbeat (``procrastinate-org/procrastinate``
``procrastinate/worker.py`` ``_update_heartbeat``: a task beside the work that updates
``last_heartbeat`` every ``update_heartbeat_interval``, 10 s by default;
``sql/queries.sql`` ``select_stalled_jobs_by_heartbeat``: jobs still ``doing`` whose worker's
heartbeat is older than ``seconds_since_heartbeat``). Its default stall timeout is 30 s for a
worker on a dedicated process; ours is 2 minutes, because a beat here shares the API's event
loop and database pool with requests.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager
from datetime import UTC, datetime, timedelta
from typing import Any, Final

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

__all__ = ["HEARTBEAT_S", "STALLED_AFTER", "beating", "stalled"]

logger = logging.getLogger(__name__)

HEARTBEAT_S: Final = 10.0
STALLED_AFTER: Final = timedelta(minutes=2)

SessionFactory = Callable[[], AbstractAsyncContextManager[AsyncSession]]


def _now() -> datetime:
    return datetime.now(UTC)


def stalled(heartbeat_at: datetime | None, since: datetime | None, *, now: datetime | None = None) -> bool:
    """Whether work last seen alive at ``heartbeat_at`` (or, never beaten, created ``since``)
    has stopped: no sign of life for ``STALLED_AFTER``."""
    seen = heartbeat_at or since
    if seen is None:
        return False
    if seen.tzinfo is None:
        seen = seen.replace(tzinfo=UTC)
    return (now or _now()) - seen > STALLED_AFTER


@contextlib.asynccontextmanager
async def beating(
    factory: SessionFactory, model: Any, row_id: str, *, every: float = HEARTBEAT_S
) -> AsyncIterator[None]:
    """Stamp ``model``'s row ``row_id`` with ``heartbeat_at`` every ``every`` seconds, on its own
    database sessions, while the block runs (the caller stamps it as the work starts)."""

    async def beat() -> None:  # the caller stamped the start; beats follow
        while True:
            await asyncio.sleep(every)
            try:
                async with factory() as db:
                    await db.execute(update(model).where(model.id == row_id).values(heartbeat_at=_now()))
                    await db.commit()
            except Exception:  # a missed beat is retried; it never stops the work
                logger.warning("heartbeat of %s %s failed", model.__tablename__, row_id, exc_info=True)

    task = asyncio.create_task(beat())
    try:
        yield
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
