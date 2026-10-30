"""Ended sessions are erased once they have been quiet for ``SESSION_RETENTION_DAYS``.

Nothing used to remove a session unless someone erased it by hand, so every conversation,
file and checkpoint was kept for ever. With ``SESSION_RETENTION_DAYS`` set, the API sweeps
every ``SESSION_RETENTION_SWEEP_S`` (an hour): a session that has ended (terminated or
failed: ``CLOSED_STATUSES``) and had no activity for that many days is erased as
``supervisor.session.erase`` erases one, a batch at a time. A session still open is never
swept, however quiet. Unset (the default), sessions are kept.

On Postgres one replica sweeps at a time (an advisory lock); erasing is idempotent, so a
sweep interrupted part way is finished by the next.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Final

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from supervisor.db.models import SessionIndexRow, SessionRow
from supervisor.temporal.contracts import CLOSED_STATUSES

__all__ = ["Retention", "RetentionSweeper", "run_retention_loop"]

logger = logging.getLogger(__name__)

_LOCK_KEY: Final = 0xA12_0D15  # pg advisory lock: "session retention sweep"
BATCH: Final = 200
"""Sessions erased per sweep at most (the rest wait for the next)."""


@dataclass(frozen=True, slots=True)
class Retention:
    days: int
    sweep_s: float = 3600.0

    @classmethod
    def from_env(cls) -> Retention | None:
        """``SESSION_RETENTION_DAYS`` (a positive number of days), else None: keep sessions."""
        raw = os.environ.get("SESSION_RETENTION_DAYS", "").strip()
        if not raw:
            return None
        days = int(raw)
        if days < 1:
            raise ValueError("SESSION_RETENTION_DAYS must be at least 1")
        sweep = float(os.environ.get("SESSION_RETENTION_SWEEP_S", "3600") or 3600)
        return cls(days=days, sweep_s=max(60.0, sweep))


class RetentionSweeper:
    def __init__(
        self,
        factory: async_sessionmaker[AsyncSession],
        retention: Retention,
        *,
        erase: Callable[[str], Awaitable[object]],
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        """``erase(session_id)``: end (if needed) and erase one session."""
        self._db = factory
        self._retention = retention
        self._erase = erase
        self._now = now

    async def expired(self, limit: int = BATCH) -> list[str]:
        """Ended sessions quiet since before the retention window, oldest first."""
        cutoff = self._now() - timedelta(days=self._retention.days)
        last_active = func.coalesce(SessionIndexRow.last_event_at, SessionRow.updated_at, SessionRow.created_at)
        async with self._db() as db:
            rows = await db.scalars(
                select(SessionRow.id)
                .outerjoin(SessionIndexRow, SessionIndexRow.session_id == SessionRow.id)
                .where(SessionRow.status.in_(CLOSED_STATUSES), last_active < cutoff)
                .order_by(last_active)
                .limit(limit)
            )
            return list(rows)

    async def sweep(self) -> int:
        """Erase what has expired (one batch); how many were erased. One replica at a time."""
        async with self._sweeping() as mine:
            if not mine:
                return 0
            erased = 0
            for session_id in await self.expired():
                try:
                    await self._erase(session_id)
                    erased += 1
                except Exception:
                    logger.warning("retention: could not erase session %s; the next sweep retries", session_id,
                                   exc_info=True)
            if erased:
                logger.info("retention: erased %d ended session(s) older than %d days", erased, self._retention.days)
            return erased

    @contextlib.asynccontextmanager
    async def _sweeping(self):
        async with self._db() as db:
            if db.bind.dialect.name != "postgresql":
                yield True
                return
            got = bool(await db.scalar(text("SELECT pg_try_advisory_lock(:key)"), {"key": _LOCK_KEY}))
            try:
                yield got
            finally:
                if got:
                    await db.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": _LOCK_KEY})


async def run_retention_loop(sweeper: RetentionSweeper, every_s: float, stop: asyncio.Event) -> None:
    """Sweep every ``every_s`` until ``stop`` is set (a failed sweep is logged and retried)."""
    while not stop.is_set():
        try:
            await sweeper.sweep()
        except Exception:
            logger.exception("retention sweep failed")
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=every_s)
