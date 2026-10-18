"""Session lifecycle operations: idle reaping and deploy drain.

These operations manage session cleanup and graceful shutdown
for deployments and resource management.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    pass

__all__ = [
    "reap_idle",
    "drain_for_deploy",
]

logger = logging.getLogger(__name__)


async def reap_idle(
    manager: Any,
    *,
    max_idle_seconds: float,
) -> list[str]:
    """Pause or terminate sessions idle longer than the threshold.

    Delegates to SessionManager.reap_idle_sessions() which handles
    the actual session inspection and cleanup.

    Args:
        manager: SessionManager instance
        max_idle_seconds: Maximum idle time before reaping

    Returns:
        List of session IDs that were reaped
    """
    logger.info(
        "starting idle session reap",
        extra={"max_idle_seconds": max_idle_seconds},
    )
    reaped = await manager.reap_idle_sessions(max_idle_seconds=max_idle_seconds)
    if reaped:
        logger.info(
            "reaped idle sessions",
            extra={"count": len(reaped), "session_ids": reaped},
        )
    return reaped


async def drain_for_deploy(
    manager: Any,
    *,
    timeout_seconds: float,
) -> None:
    """Stop accepting work and wait for in-flight turns to settle.

    Used during graceful shutdown for deployments:
    - Sticky sessions are paused
    - Stateless sessions finish the turn and stop

    Callers must set `draining=True` first so `/readyz` returns 503
    and create refuses new sessions.

    Args:
        manager: SessionManager instance
        timeout_seconds: Maximum time to wait for sessions to settle
    """
    logger.info(
        "starting drain for deploy",
        extra={"timeout_seconds": timeout_seconds},
    )
    await manager.drain(timeout_seconds=timeout_seconds)
    logger.info("drain complete")
