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
import time
from typing import Final

from supervisor.memory_service.notebooks.ops import (
    KEEP_PROMOTED_MS,
    KEEP_SET_ASIDE_MS,
    PROMOTE_ATTEMPTS,
    NotebookOps,
)
from supervisor.memory_service.notebooks.store import now_ms

__all__ = ["InboxDrainer"]

logger = logging.getLogger(__name__)

NOTEBOOKS_PER_PASS: Final = 64
"""Notebooks one pass looks at; it goes round again while any were full."""
CONCURRENCY: Final = 8
"""Notebooks promoted at once (each its own transaction, and its own connection)."""
BACKOFF_S: Final = 5.0
MAX_BACKOFF_S: Final = 300.0
"""A notebook whose promotion failed is tried again after 5 s, doubling to 5 minutes."""
FORGET_AFTER_S: Final = 3600.0
"""A notebook still failing keeps its backoff until it lands; one no drain has reached for
an hour past its retry (erased, or drained by another replica) is forgotten."""
PRUNE_EVERY_S: Final = 60.0
PASSES_PER_DRAIN: Final = 16
"""Passes one drain makes at most: under steady load the queue is never empty, and a
notebook one drain passed over is tried again in the next, never left skipped."""


class InboxDrainer:
    def __init__(self, ops: NotebookOps, *, concurrency: int = CONCURRENCY) -> None:
        self._ops = ops
        self._wake = asyncio.Event()
        self._gate = asyncio.Semaphore(concurrency)
        self._failing: dict[str, tuple[int, float]] = {}
        """Notebooks whose promotion failed: (failures in a row, when to try again)."""
        self._pruned_at = float("-inf")

    def kick(self) -> None:
        """A capture was queued: drain now, not at the next pass."""
        self._wake.set()

    async def drain(self) -> int:
        """Promote every notebook's queued captures; the captures landed. A notebook whose
        promotion keeps failing is tried again later, backing off, so it never holds up
        the others' (nor fills a pass with failures)."""
        landed = 0
        passed: set[str] = set()  # this drain's notebooks that landed nothing (failed, or taken elsewhere)
        for _ in range(PASSES_PER_DRAIN):  # bounded: the next drain starts with nothing skipped
            await self._prune()  # here too: under steady load a drain may never end
            skip = [*self._backing_off(), *passed]
            notebooks = await self._ops.store.pending_notebooks(limit=NOTEBOOKS_PER_PASS, skip=skip)
            if not notebooks:
                break
            done = await asyncio.gather(*(self._promote(n) for n in notebooks))
            landed += sum(c for c in done if c)
            # Past those that landed nothing to the next ones: they never hold up the rest.
            passed.update(n for n, c in zip(notebooks, done, strict=True) if not c)
        return landed

    def _backing_off(self) -> list[str]:
        """The notebooks to leave for now; one whose backoff ran out long ago is forgotten
        (erased, say, or emptied elsewhere): nothing is kept for it."""
        now = time.monotonic()
        for notebook_id, (_, until) in list(self._failing.items()):
            if until + FORGET_AFTER_S < now:
                del self._failing[notebook_id]
        return [n for n, (_, until) in self._failing.items() if until > now]

    async def _promote(self, notebook_id: str) -> int:
        async with self._gate:
            try:
                captures, _ = await self._ops.promote(notebook_id)
            except Exception:
                failures = self._failing.get(notebook_id, (0, 0.0))[0] + 1
                delay = min(MAX_BACKOFF_S, BACKOFF_S * 2 ** (failures - 1))
                self._failing[notebook_id] = (failures, time.monotonic() + delay)
                logger.warning(
                    "promoting notebook %s's queued captures failed (%d in a row; next try in %.0f s)",
                    notebook_id, failures, delay, exc_info=True,
                )
                return 0
            self._failing.pop(notebook_id, None)
            return captures

    async def _prune(self) -> None:
        """Every notebook's promoted rows past their window (``ops.KEEP_PROMOTED_MS``), at
        most once a ``PRUNE_EVERY_S``: a notebook captured into once is pruned too."""
        if time.monotonic() - self._pruned_at < PRUNE_EVERY_S:
            return
        self._pruned_at = time.monotonic()
        try:
            now = now_ms()
            await self._ops.store.prune_inbox(
                before=now - KEEP_PROMOTED_MS, set_aside_before=now - KEEP_SET_ASIDE_MS, give_up_at=PROMOTE_ATTEMPTS
            )
        except Exception:
            logger.warning("pruning promoted captures failed", exc_info=True)

    async def run(self, interval_s: float) -> None:
        """Drain whenever a capture is queued here, and every ``interval_s`` (for those
        queued on other replicas), until cancelled; then captures land as they are queued
        again (``NotebookOps.on_queued``)."""
        self._ops.on_queued = self.kick
        try:
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
        finally:
            if self._ops.on_queued == self.kick:
                self._ops.on_queued = None
