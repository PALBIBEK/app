"""Which worker holds each session: its runtime, its sandbox, and the right to save them.

A worker takes the hold when it creates, forks or adopts a session. A worker that lost
it (adopted away while it was frozen or cut off) can wake up still holding the session's
old sandbox; its writes must then be refused, or its checkpoints would overwrite the new
worker's and its tool results would describe a sandbox that no longer serves
(pi-temporal's fence). A tool call's start and end carry a ``Fence``: checked in the
journal write's own transaction, so a takeover and that write never interleave. A
checkpoint's files go to the workspace store, outside that transaction: it checks before
each file, so a writer that stalls between its check and its write can still land that
one file (the new worker's first checkpoint saves every file again).
"""

from __future__ import annotations

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from supervisor.db.models import SessionRow
from supervisor.journal.writer import Fence

__all__ = ["SessionHolds"]


class SessionHolds:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._db = session_factory
        self.holder: str | None = None
        """This worker's queue, set when it starts serving; None outside a worker."""

    async def take(self, session_id: str) -> None:
        """This worker holds ``session_id`` from now on."""
        if self.holder is None:
            return
        async with self._db() as db, db.begin():
            await db.execute(
                update(SessionRow).where(SessionRow.id == session_id).values(holder=self.holder)
            )

    async def held(self, session_id: str) -> bool:
        """Whether this worker may still write ``session_id``'s sandbox state. A session no
        worker has taken yet (made before holds existed) is held by whoever serves it."""
        if self.holder is None:
            return True
        async with self._db() as db:
            holder = (
                await db.execute(select(SessionRow.holder).where(SessionRow.id == session_id))
            ).scalar_one_or_none()
        return holder is None or holder == self.holder

    def fence(self, session_id: str) -> Fence | None:
        """What a journal write takes to be refused once ``session_id`` moves; None outside
        a worker."""
        return None if self.holder is None else Fence(session_id, self.holder)
