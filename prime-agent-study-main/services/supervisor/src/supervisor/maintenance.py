"""Background maintenance loops, run wherever session runtimes live (the worker)."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable

from supervisor.session import SessionManager

logger = logging.getLogger(__name__)


async def approval_sweeper(
    manager: SessionManager, *, should_stop: Callable[[], bool], interval_s: float = 5.0
) -> None:
    """Expire overdue approvals using each session's pinned on_timeout policy."""
    while not should_stop():
        try:
            if expired := await manager.expire_overdue_approvals():
                logger.info("approval-sweeper: expired %d approvals", len(expired))
        except Exception:
            logger.exception("approval sweeper failed")
        await asyncio.sleep(interval_s)


async def idle_reaper(
    manager: SessionManager,
    *,
    should_stop: Callable[[], bool],
    max_idle_s: float,
    interval_s: float,
) -> None:
    """Pause sticky and stop stateless runtimes idle longer than ``max_idle_s``."""
    interval_s = max(5.0, interval_s)
    while not should_stop():
        try:
            if reaped := await manager.reap_idle_sessions(max_idle_seconds=max_idle_s):
                logger.info("idle reaper: reaped %d sessions %s", len(reaped), reaped[:8])
        except Exception:
            logger.exception("idle reaper failed")
        await asyncio.sleep(interval_s)
