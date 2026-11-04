"""Which blobs the journal keeps, and which events name them.

A blob (a large payload, a lost sandbox's logs) is named by its content's hash and kept
once, however many events name it. Whether one is still used was found by scanning every
journal's payloads, twice per session erased (a retention sweep erases many). Now:

- ``journal_blobs`` has a row per blob kept, from when it was stored (``kept``);
- ``journal_blob_refs`` has a row per event that names one, written in the event's own
  transaction (``add_refs``): whether a blob is used is an indexed lookup (``in_use``);
- the orphan sweep (``BlobSweeper``) deletes the blobs no event names that were stored
  more than ``grace_s`` ago: a blob is stored before its event's transaction, so one
  that write left behind (it failed, or lost a race) is otherwise kept for ever. The
  grace covers a write still on its way to commit.

Blobs stored before these tables were read from the journal by migration 0037; one
already orphaned then is not known (the object store cannot be listed) and stays.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator, Iterable
from datetime import UTC, datetime, timedelta
from typing import Any, Final

from sqlalchemy import delete, exists, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from supervisor.db.models import JournalBlobRefRow, JournalBlobRow
from supervisor.journal.blobs import BlobStore

__all__ = [
    "GRACE_S",
    "BlobSweeper",
    "add_refs",
    "drop_refs",
    "forget_unused",
    "in_use",
    "kept",
    "refs_of",
    "run_blob_sweep_loop",
]

logger = logging.getLogger(__name__)

GRACE_S: Final = 24 * 3600.0
_FIELDS: Final = ("_blob_ref", "logs_ref")
_CHUNK: Final = 500
_LOCK_KEY: Final = 0x5E5_0B10  # pg_advisory_lock key: one replica sweeps at a time


def refs_of(payload: dict[str, Any]) -> set[str]:
    """The blobs a stored payload names."""
    return {ref for field in _FIELDS if isinstance(ref := payload.get(field), str)}


async def kept(factory: async_sessionmaker[AsyncSession], ref: str) -> None:
    """Record that the blob ``ref`` is kept, as of now: before the blob is stored, so a
    sweep that finds it old and unused at that moment sees it fresh instead (or, if it
    got there first, puts the bytes back: ``_forget``)."""
    now = datetime.now(UTC)
    async with factory() as db:
        touched = await db.execute(update(JournalBlobRow).where(JournalBlobRow.ref == ref).values(stored_at=now))
        if touched.rowcount:  # type: ignore[attr-defined]
            await db.commit()
            return
        db.add(JournalBlobRow(ref=ref, stored_at=now))
        with contextlib.suppress(IntegrityError):  # a racing writer recorded it
            await db.commit()


def add_refs(db: AsyncSession, session_id: str, seq: int, payload: dict[str, Any]) -> None:
    """Record, in ``db``'s transaction, the blobs the event at ``seq`` names."""
    for ref in refs_of(payload):
        db.add(JournalBlobRefRow(ref=ref, session_id=session_id, seq=seq))


async def drop_refs(db: AsyncSession, session_id: str, seq: int | None = None) -> None:
    """Forget what the session's events (or the one at ``seq``) name."""
    query = delete(JournalBlobRefRow).where(JournalBlobRefRow.session_id == session_id)
    if seq is not None:
        query = query.where(JournalBlobRefRow.seq == seq)
    await db.execute(query)


async def in_use(db: AsyncSession, refs: Iterable[str]) -> set[str]:
    """Which of ``refs`` an event still names."""
    wanted = sorted(set(refs))
    used: set[str] = set()
    for i in range(0, len(wanted), _CHUNK):
        used.update(
            await db.scalars(select(JournalBlobRefRow.ref).where(JournalBlobRefRow.ref.in_(wanted[i : i + _CHUNK])))
        )
    return used


async def forget_unused(
    factory: async_sessionmaker[AsyncSession], blobs: BlobStore, refs: Iterable[str], *, stored_before: datetime | None = None
) -> int:
    """Delete each of ``refs`` no event names (and, with ``stored_before``, recorded before
    then): its record, then the blob; how many blobs were deleted."""
    deleted = 0
    for ref in sorted(set(refs)):
        deleted += await _forget(factory, blobs, ref, stored_before)
    return deleted


async def _forget(
    factory: async_sessionmaker[AsyncSession], blobs: BlobStore, ref: str, stored_before: datetime | None
) -> int:
    """One blob. A writer storing the same bytes now records it first (``kept``): the
    record goes only while unused and old, and if a writer recorded it again while the
    blob was being deleted, the bytes are put back (they are its content: the same)."""
    unused = ~exists().where(JournalBlobRefRow.ref == ref)
    async with factory() as db:
        query = delete(JournalBlobRow).where(JournalBlobRow.ref == ref, unused)
        if stored_before is not None:
            query = query.where(JournalBlobRow.stored_at < stored_before)
        gone = (await db.execute(query)).rowcount  # type: ignore[attr-defined]
        if not gone:
            # Not deleted: in use, recent, or never recorded (kept before the record
            # existed; erasing a session may still delete one of those when unused).
            legacy = stored_before is None and await db.get(JournalBlobRow, ref) is None
            if not legacy or ref in await in_use(db, [ref]):
                return 0
        await db.commit()
    try:
        data: bytes | None = await blobs.get(ref)
    except FileNotFoundError:
        return 0
    removed = int(await blobs.delete(ref))
    async with factory() as db:
        if await db.get(JournalBlobRow, ref) is not None and data is not None:
            await blobs.put(ref, data)  # a writer stored it again meanwhile
            return 0
    return removed


class BlobSweeper:
    """Deletes the blobs no event names, stored more than ``grace_s`` ago."""

    def __init__(
        self, factory: async_sessionmaker[AsyncSession], blobs: BlobStore, *, grace_s: float = GRACE_S
    ) -> None:
        self._db, self._blobs, self._grace_s = factory, blobs, grace_s

    async def sweep(self, *, now: datetime | None = None, batch: int = _CHUNK) -> int:
        """One pass (one replica at a time on Postgres); how many blobs were deleted."""
        cutoff = (now or datetime.now(UTC)) - timedelta(seconds=self._grace_s)
        deleted = 0
        async with self._sweeping() as mine:
            if not mine:
                return 0
            after = ""
            while True:
                async with self._db() as db:
                    old = list(
                        await db.scalars(
                            select(JournalBlobRow.ref)
                            .where(JournalBlobRow.stored_at < cutoff, JournalBlobRow.ref > after)
                            .order_by(JournalBlobRow.ref)
                            .limit(batch)
                        )
                    )
                    if not old:
                        break
                after = old[-1]
                deleted += await forget_unused(self._db, self._blobs, old, stored_before=cutoff)
        if deleted:
            logger.info("journal: deleted %d blob(s) no event names", deleted)
        return deleted

    @contextlib.asynccontextmanager
    async def _sweeping(self) -> AsyncIterator[bool]:
        async with self._db() as db:
            if db.get_bind().dialect.name != "postgresql":
                yield True
                return
            got = bool(await db.scalar(text("SELECT pg_try_advisory_lock(:key)"), {"key": _LOCK_KEY}))
            try:
                yield got
            finally:
                if got:
                    await db.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": _LOCK_KEY})


async def run_blob_sweep_loop(sweeper: BlobSweeper, every_s: float, stop: asyncio.Event) -> None:
    """Sweep every ``every_s`` until ``stop`` is set (a failed sweep is logged and retried)."""
    while not stop.is_set():
        try:
            await sweeper.sweep()
        except Exception:
            logger.exception("journal blob sweep failed")
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=every_s)
