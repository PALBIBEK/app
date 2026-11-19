"""Every worker's capture sweeper (G3): runs the capture jobs no session is running.

A session's capture activity enqueues its burst and runs it at once (``SessionMemory.
capture``). What it leaves behind is a job here: one that failed and backs off, one whose
worker died holding its lease (the lease runs out), or one queued but never claimed. Each
worker claims those in turn, fairly across tenants: the memory service lists the tenants
with work, the fewest running first, and a round takes at most one job per tenant. A
pending job is left to whoever queued it for ``PENDING_GRACE_S``.

All state is the memory service's; a worker holds only the leases it is running, and gives
them back when it stops (``SessionMemory.run_capture`` releases on cancellation).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
from collections.abc import Callable
from typing import Final

from supervisor.memory.client import NotebookClient
from supervisor.memory.turn import CAPTURE_LEASE_S, SessionMemory, worker_identity
from supervisor.memory_service.capture.queue import CaptureLease

__all__ = ["PENDING_GRACE_S", "capture_sweeper", "sweep_concurrency"]

logger = logging.getLogger(__name__)

SWEEP_EVERY_S: Final = 5.0
PENDING_GRACE_S: Final = 60.0
"""A pending job younger than this is its session's activity's to run (it claims it at once)."""
CONCURRENCY_ENV: Final = "MEMORY_CAPTURE_SWEEP_CONCURRENCY"
DEFAULT_CONCURRENCY: Final = 2
LIST_PER_SLOT: Final = 4
"""Tenants listed per free slot: room to move on past the ones other workers emptied."""


def sweep_concurrency(environ: dict[str, str] | None = None) -> int:
    """Capture jobs one worker's sweeper runs at once (``MEMORY_CAPTURE_SWEEP_CONCURRENCY``,
    2). Each holds a model call's tenant slot while it extracts (``memory.fairness``)."""
    raw = (environ if environ is not None else os.environ).get(CONCURRENCY_ENV, "").strip()
    if not raw:
        return DEFAULT_CONCURRENCY
    try:
        value = int(raw)
    except ValueError:
        raise ValueError(f"{CONCURRENCY_ENV} must be a whole number, not {raw!r}") from None
    if value < 0:
        raise ValueError(f"{CONCURRENCY_ENV} must be 0 (off) or more, not {value}")
    return value


async def capture_sweeper(
    memory: SessionMemory,
    queue: NotebookClient,
    *,
    should_stop: Callable[[], bool],
    concurrency: int | None = None,
    interval_s: float = SWEEP_EVERY_S,
) -> None:
    """Claim and run capture jobs until ``should_stop``; the running ones are cancelled
    (their leases released) when it ends."""
    limit = sweep_concurrency() if concurrency is None else concurrency
    if limit <= 0:
        return
    running: set[asyncio.Task[int]] = set()
    try:
        while not should_stop():
            free = limit - len(running)
            if free > 0:
                for lease in await _claim(queue, free):
                    task = asyncio.create_task(memory.run_capture(lease), name=f"capture-{lease.job.job_id}")
                    running.add(task)
                    task.add_done_callback(running.discard)
            await asyncio.sleep(interval_s)
    finally:
        for task in list(running):
            task.cancel()
        with contextlib.suppress(Exception):
            await asyncio.gather(*running, return_exceptions=True)


async def _claim(queue: NotebookClient, free: int) -> list[CaptureLease]:
    """Up to ``free`` jobs, one per tenant a pass, in the service's fair order: a tenant
    gets a second only when every tenant listed has had its turn (a lone backlog uses every
    free slot). More tenants are listed than slots, so a worker that loses one tenant's last
    job to another worker takes the next tenant's. A call that fails ends the round with the
    leases taken so far, every one of them run: none is left held for nothing until it runs
    out. The next round tries again."""
    leases: list[CaptureLease] = []
    try:
        tenants = await queue.capture_tenants(limit=free * LIST_PER_SLOT, pending_grace_s=PENDING_GRACE_S)
        while tenants and len(leases) < free:
            served = []
            for tenant in tenants:
                lease = await queue.capture_claim(
                    tenant_id=tenant, owner=worker_identity(), lease_s=CAPTURE_LEASE_S, pending_grace_s=PENDING_GRACE_S
                )
                if lease is not None:
                    leases.append(lease)
                    served.append(tenant)
                if len(leases) >= free:
                    break
            tenants = served  # only those that had work, again
    except Exception:  # the service unreachable, a tenant's rate limit
        logger.warning("capture sweep failed", exc_info=True)
    return leases
