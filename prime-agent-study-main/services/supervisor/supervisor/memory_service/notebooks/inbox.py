"""The inbox's drainer: queued automatic captures become facts (G2, hot spot S1 of
``docs/memory-gaps.md``).

A capture is one insert into the inbox, with no lock on its notebook
(``NotebookOps.observe``). The drainer lands each notebook's queued captures in a batch,
under that notebook's lock once per batch (``NotebookOps.promote``), so a notebook that
many sessions capture into at once takes one lock for many captures, where each capture
used to take its own and wait for the rest.

Every replica runs one. A capture queued on this replica wakes it at once (``kick``), so
the facts are recallable within one promotion; a capture queued on another is found by
the replica that queued it, or by the next pass here (``interval_s``). Replicas promoting
one notebook take turns on its lock, and the later one finds the queue empty.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Final

from supervisor.memory_service.notebooks.ops import NotebookOps

__all__ = ["InboxDrainer"]

logger = logging.getLogger(__name__)

NOTEBOOKS_PER_PASS: Final = 64
"""Notebooks one pass looks at; it goes round again while any were full."""
CONCURRENCY: Final = 8
"""Notebooks promoted at once (each its own transaction, and its own connection)."""


class InboxDrainer:
    def __init__(self, ops: NotebookOps, *, concurrency: int = CONCURRENCY) -> None:
        self._ops = ops
        self._wake = asyncio.Event()
        self._gate = asyncio.Semaphore(concurrency)

    def kick(self) -> None:
        """A capture was queued: drain now, not at the next pass."""
        self._wake.set()

    async def drain(self) -> int:
        """Promote every notebook's queued captures; the captures landed."""
        landed = 0
        while notebooks := await self._ops.store.pending_notebooks(limit=NOTEBOOKS_PER_PASS):
            done = await asyncio.gather(*(self._promote(n) for n in notebooks))
            landed += sum(done)
            if not any(done):
                break  # promoting elsewhere, or failing: the next pass tries again
        return landed

    async def _promote(self, notebook_id: str) -> int:
        async with self._gate:
            try:
                captures, _ = await self._ops.promote(notebook_id)
            except Exception:
                logger.warning("promoting notebook %s's queued captures failed", notebook_id, exc_info=True)
                return 0
            return captures

    async def run(self, interval_s: float) -> None:
        """Drain whenever a capture is queued here, and every ``interval_s`` (for those
        queued on other replicas), until cancelled."""
        self._ops.on_queued = self.kick
        while True:
            self._wake.clear()
            try:
                await self.drain()
            except Exception:
                logger.warning("inbox drain pass failed", exc_info=True)
            try:
                await asyncio.wait_for(self._wake.wait(), interval_s)
            except TimeoutError:
                pass
