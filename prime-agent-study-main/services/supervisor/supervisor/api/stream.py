"""Following a session as it happens: its journalled events, the reply being written, and
whether it has ended, as typed items; each endpoint writes them in its own protocol (our
wire, ``supervisor.api.events``; AG-UI, ``supervisor.api.agui``).

Two followers. ``follow_journal`` reads the journal, so it serves a session running in any
worker and from any API replica: woken by the process's ``JournalWatch`` when it has one,
polling otherwise. ``follow_live`` serves an in-process session from its manager's live
queue, subscribed *before* the journal is replayed and skipping seq <= cursor after, so the
handover yields neither gaps nor duplicates.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from typing import Final

from supervisor.agent.events import Envelope
from supervisor.journal.partials import Partial, PartialOutputs
from supervisor.journal.reader import JournalReader
from supervisor.journal.watch import JournalWatch
from supervisor.session import Dropped, LiveSubscription, SessionManager
from supervisor.temporal.contracts import CLOSED_STATUSES

__all__ = ["ENDED", "PAGE", "PING", "Ended", "Item", "Ping", "follow_journal", "follow_live"]

logger = logging.getLogger(__name__)

PAGE: Final = 500
"""Events read at once: a client starting from the beginning of a long session gets it a page
at a time, not the whole journal in one read held in memory."""
JOURNAL_POLL_SECS: Final = 0.25
ENDED_CHECK_SECS: Final = 2.0
"""How often a quiet stream asks whether its session has ended."""


@dataclass(frozen=True, slots=True)
class Ping:
    """Nothing happened for a heartbeat: a frame keeps the connection open."""


@dataclass(frozen=True, slots=True)
class Ended:
    """The session has ended and every event of it was given: nothing will follow."""


PING: Final = Ping()
ENDED: Final = Ended()

type Item = Envelope | Partial | Ping | Ended
"""An event as journalled; the reply being written (not journalled, so never resumed from);
a heartbeat; the end."""


async def _partial(partials: PartialOutputs, session_id: str) -> Partial | None:
    try:
        return await partials.get(session_id)
    except Exception:
        logger.debug("partial output read failed session=%s", session_id, exc_info=True)
        return None


def _key(partial: Partial) -> tuple[str, int, int]:
    return partial.run_id, partial.step, partial.updated_ms


async def follow_journal(
    *,
    session_id: str,
    cursor: int,
    reader: JournalReader,
    is_disconnected: Callable[[], Awaitable[bool]] | None = None,
    has_ended: Callable[[], Awaitable[bool]] | None = None,
    heartbeat_secs: float,
    poll_secs: float = JOURNAL_POLL_SECS,
    ended_check_secs: float = ENDED_CHECK_SECS,
    partials: PartialOutputs | None = None,
    watch: JournalWatch | None = None,
) -> AsyncIterator[Item]:
    """The session's events after ``cursor``, then as they are written.

    ``PING`` after ``heartbeat_secs`` with nothing to give. With ``partials``, the reply the
    model is writing too, each time it grows. With
    ``has_ended``, a quiet follower asks every ``ended_check_secs`` whether the session has
    ended; once it has, the journal is read once more (what was written before it ended comes
    first), then ``ENDED``. With ``is_disconnected``, a client that went away stops it within
    a poll. With ``watch``, it reads only when its session changed (the one watcher every
    stream shares), and the session has ended when its status is a closed one.
    """
    hb = heartbeat_secs
    if watch is not None:
        async for item in _watched(
            session_id=session_id,
            cursor=cursor,
            reader=reader,
            watch=watch,
            is_disconnected=is_disconnected,
            ended_from_status=has_ended is not None,
            heartbeat_secs=hb,
            partials=partials,
        ):
            yield item
        return
    idle, unchecked = 0.0, ended_check_secs  # asks on the first quiet poll
    ending = False
    shown: tuple[str, int, int] | None = None
    while is_disconnected is None or not await is_disconnected():
        batch = await reader.read(session_id, since=cursor, limit=PAGE)
        for env in batch:
            yield env
            cursor = env.seq
        if batch:
            idle = 0.0
            continue
        if ending:
            yield ENDED
            return
        if has_ended is not None and unchecked >= ended_check_secs:
            unchecked = 0.0
            if ending := await has_ended():
                continue
        partial = await _partial(partials, session_id) if partials is not None else None
        if partial is not None and _key(partial) != shown:
            shown, idle = _key(partial), 0.0
            yield partial
            continue
        if idle >= hb:
            yield PING
            idle = 0.0
        await asyncio.sleep(poll_secs)
        idle += poll_secs
        unchecked += poll_secs


async def _watched(
    *,
    session_id: str,
    cursor: int,
    reader: JournalReader,
    watch: JournalWatch,
    is_disconnected: Callable[[], Awaitable[bool]] | None,
    ended_from_status: bool,
    heartbeat_secs: float,
    partials: PartialOutputs | None,
) -> AsyncIterator[Item]:
    shown: tuple[str, int, int] | None = None
    seen = await watch.changed(session_id, None, timeout=heartbeat_secs)
    while is_disconnected is None or not await is_disconnected():
        if seen.last_seq > cursor and (batch := await reader.read(session_id, since=cursor, limit=PAGE)):
            for env in batch:
                yield env
                cursor = env.seq
            continue  # what was written while those were given
        if ended_from_status and (seen.status is None or seen.status in CLOSED_STATUSES):
            yield ENDED
            return
        if partials is not None and seen.partial_ms is not None:
            partial = await _partial(partials, session_id)
            if partial is not None and _key(partial) != shown:
                shown = _key(partial)
                yield partial
        now = await watch.changed(session_id, seen, timeout=heartbeat_secs)
        if now == seen:
            yield PING
        seen = now


async def follow_live(
    *,
    session_id: str,
    cursor: int,
    manager: SessionManager,
    reader: JournalReader,
    is_disconnected: Callable[[], bool] | None = None,
    heartbeat_secs: float,
) -> AsyncIterator[Envelope | Ping]:
    """An in-process session's events after ``cursor``, then as its manager publishes them;
    it stops when the client goes away or is dropped for being too slow."""
    hb = heartbeat_secs
    gone = is_disconnected or (lambda: False)
    sub: LiveSubscription = manager.subscribe_live(session_id)
    live_cursor = cursor
    try:
        while page := await reader.read(session_id, since=live_cursor, limit=PAGE):
            for env in page:
                if gone():
                    return
                yield env
                live_cursor = env.seq
        while True:
            if sub.dropped or gone():
                if sub.dropped:
                    logger.info("dropping slow SSE client session=%s", session_id)
                return
            try:
                item = await asyncio.wait_for(sub.queue.get(), timeout=hb)
            except TimeoutError:
                yield PING
                continue
            if isinstance(item, Dropped):
                return
            if item.seq <= live_cursor:
                continue
            yield item
            live_cursor = item.seq
    finally:
        manager.unsubscribe_live(session_id, sub)
