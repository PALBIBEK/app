"""Idle reaping and deploy drain."""

from __future__ import annotations

from typing import Any


async def reap_idle(manager: Any, *, max_idle_seconds: float) -> list[str]:
    """Pause or terminate sessions idle longer than the threshold. Returns ids reaped."""
    return await manager.reap_idle_sessions(max_idle_seconds=max_idle_seconds)


async def drain_for_deploy(manager: Any, *, timeout_seconds: float) -> None:
    """Stop accepting work and wait for in-flight turns to settle.

    Sticky sessions are paused; stateless sessions finish the turn and stop.
    Callers must set ``draining=True`` first so ``/readyz`` returns 503 and
    create refuses new sessions.
    """
    await manager.drain(timeout_seconds=timeout_seconds)
