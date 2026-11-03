"""What tells an event stream that its session changed: one watcher per process.

Each open stream used to poll the database itself, four times a second, for new events
and for the reply being written, and every two seconds for whether its session ended:
500 open streams were thousands of queries a second, and the API's p95 went to 2.5 s.

A ``JournalWatch`` asks once for every session any stream in the process follows: one
query (in chunks) reads each one's last seq, status and partial reply time, and wakes
the streams whose session changed. On Postgres it also listens for the notification
each journal write and partial reply sends on commit (``notify``), so a stream wakes
as soon as the write lands; the shared poll then only backs that up, every
``backstop_secs``, in case a notification is lost (the listening connection dropped).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, Final

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from supervisor.db.models import PartialOutputRow, SessionRow

__all__ = ["CHANNEL", "JournalWatch", "Seen", "notify"]

logger = logging.getLogger(__name__)

CHANNEL: Final = "yal_journal"
"""The Postgres channel journal writes and partial replies notify on (payload: session id)."""

_CHUNK: Final = 500
_RELISTEN_SECS: Final = 30.0


async def notify(db: AsyncSession, session_id: str) -> None:
    """Tell every process's watch that ``session_id`` changed, once ``db``'s transaction
    commits (Postgres delivers a notification at commit, and drops it on rollback)."""
    if db.get_bind().dialect.name == "postgresql":
        await db.execute(text("SELECT pg_notify(:channel, :id)"), {"channel": CHANNEL, "id": session_id})


@dataclass(frozen=True, slots=True)
class Seen:
    """What a stream has seen of its session."""

    last_seq: int
    status: str | None  # None: there is no such session (it was erased)
    partial_ms: int | None  # when the reply being written last grew; None: none is


class _Watched:
    __slots__ = ("changed", "seen", "stale", "streams")

    def __init__(self) -> None:
        self.streams = 0
        self.seen: Seen | None = None
        self.changed = asyncio.Event()
        self.stale = False  # no stream waited on it at the last round


class JournalWatch:
    def __init__(
        self,
        factory: async_sessionmaker[AsyncSession],
        *,
        poll_secs: float = 0.25,
        backstop_secs: float = 2.0,
        listen: bool = True,
    ) -> None:
        """``poll_secs``: how often the shared poll runs while not listening;
        ``backstop_secs``: while listening for notifications."""
        self._factory = factory
        self._poll_secs = poll_secs
        self._backstop_secs = backstop_secs
        self._listen = listen
        self._sessions: dict[str, _Watched] = {}
        self._dirty: set[str] = set()
        self._kick = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self.listening = False

    async def changed(self, session_id: str, seen: Seen | None, *, timeout: float) -> Seen:
        """What there is now of ``session_id`` once it differs from ``seen`` (as soon as
        it is known when ``seen`` is None), or after ``timeout`` seconds, whichever is
        first. Streams starting together share the one query that tells them."""
        self._ensure_running()
        watched = self._sessions.setdefault(session_id, _Watched())
        watched.streams += 1
        watched.stale = False
        try:
            if watched.seen is None:
                self._dirty.add(session_id)
                self._kick.set()
            loop = asyncio.get_running_loop()
            deadline = loop.time() + timeout
            while watched.seen is None or watched.seen == seen:
                left = deadline - loop.time()
                if left <= 0:
                    break
                watched.changed.clear()
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(watched.changed.wait(), timeout=left)
            return watched.seen if watched.seen is not None else await self._read_one(session_id)
        finally:
            watched.streams -= 1

    async def close(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    # --- the watcher ---------------------------------------------------------------

    def _ensure_running(self) -> None:
        loop = asyncio.get_running_loop()
        if self._task is not None and not self._task.done() and self._task.get_loop() is loop:
            return
        if self._task is None or self._task.get_loop() is not loop:  # a new event loop (tests)
            self._sessions, self._dirty, self._kick = {}, set(), asyncio.Event()
        self._task = loop.create_task(self._run(), name="journal-watch")

    async def _run(self) -> None:
        listener: _Listener | None = None
        loop = asyncio.get_running_loop()
        listen_at = full_at = loop.time()
        try:
            while True:
                if loop.time() >= full_at:
                    # The round's end: every session (what no notification tells, such as
                    # a status change, or one lost), and whether it still listens.
                    if listener is not None and not await listener.alive():
                        await listener.close()
                        listener = None  # reopened after a pause; poll fast until then
                    if self._listen and listener is None and loop.time() >= listen_at:
                        listener = await _Listener.open(self._factory, self._on_notify)
                        listen_at = loop.time() + _RELISTEN_SECS
                    self.listening = listener is not None
                    self._forget_unwatched()
                    ids = set(self._sessions)
                    full_at = loop.time() + (self._backstop_secs if self.listening else self._poll_secs)
                else:
                    ids = set(self._dirty)  # a notification or a new stream: those sessions
                self._dirty -= ids
                if ids:
                    await self._refresh(ids)
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(self._kick.wait(), timeout=max(0.0, full_at - loop.time()))
                self._kick.clear()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("journal watch stopped; streams fall back to their timeouts")
        finally:
            self.listening = False
            if listener is not None:
                await listener.close()

    def _on_notify(self, session_id: str) -> None:
        if session_id in self._sessions:
            self._dirty.add(session_id)
            self._kick.set()

    def _forget_unwatched(self) -> None:
        """Drop sessions no stream has waited on for a whole round: one that asks again
        at once (it was sending what changed) keeps what it had."""
        for session_id, watched in list(self._sessions.items()):
            if watched.streams:
                watched.stale = False
            elif watched.stale:
                del self._sessions[session_id]
                self._dirty.discard(session_id)
            else:
                watched.stale = True

    async def _refresh(self, session_ids: Iterable[str]) -> None:
        try:
            now = await self._read(list(session_ids))
        except Exception:
            logger.warning("journal watch refresh failed", exc_info=True)
            return
        for session_id, seen in now.items():
            watched = self._sessions.get(session_id)
            if watched is not None and seen != watched.seen:
                watched.seen = seen
                watched.changed.set()

    async def _read_one(self, session_id: str) -> Seen:
        return (await self._read([session_id]))[session_id]

    async def _read(self, session_ids: list[str]) -> dict[str, Seen]:
        found: dict[str, Seen] = {s: Seen(-1, None, None) for s in session_ids}
        async with self._factory() as db:
            for i in range(0, len(session_ids), _CHUNK):
                chunk = session_ids[i : i + _CHUNK]
                rows = await db.execute(
                    select(SessionRow.id, SessionRow.last_seq, SessionRow.status, PartialOutputRow.updated_ms)
                    .outerjoin(PartialOutputRow, PartialOutputRow.session_id == SessionRow.id)
                    .where(SessionRow.id.in_(chunk))
                )
                for session_id, last_seq, status, partial_ms in rows:
                    found[session_id] = Seen(
                        int(last_seq), str(status), None if partial_ms is None else int(partial_ms)
                    )
        return found


class _Listener:
    """A connection listening on ``CHANNEL`` (Postgres with asyncpg only)."""

    def __init__(self, engine: AsyncEngine, conn: Any, raw: Any, callback: Any) -> None:
        self._engine, self._conn, self._raw, self._callback = engine, conn, raw, callback

    @classmethod
    async def open(cls, factory: async_sessionmaker[AsyncSession], on_notify: Any) -> _Listener | None:
        engine = factory.kw.get("bind")
        if not isinstance(engine, AsyncEngine) or engine.dialect.name != "postgresql":
            return None
        try:
            conn = await engine.connect()
            raw = (await conn.get_raw_connection()).driver_connection
            if raw is None or not hasattr(raw, "add_listener"):
                await conn.close()
                return None

            def callback(_conn: Any, _pid: int, _channel: str, payload: str) -> None:
                on_notify(payload)

            await raw.add_listener(CHANNEL, callback)
        except Exception:
            logger.warning("journal watch cannot listen; polling instead", exc_info=True)
            return None
        return cls(engine, conn, raw, callback)

    async def alive(self) -> bool:
        """Whether it still listens: a connection the server ended is only noticed when
        it is used, so it is asked something."""
        if self._raw.is_closed():
            return False
        try:
            await asyncio.wait_for(self._raw.execute("SELECT 1"), timeout=5)
        except Exception:
            logger.info("journal watch lost its listening connection", exc_info=True)
            return False
        return True

    async def close(self) -> None:
        with contextlib.suppress(Exception):
            if not self._raw.is_closed():
                await self._raw.remove_listener(CHANNEL, self._callback)
        with contextlib.suppress(Exception):
            await self._conn.close()
