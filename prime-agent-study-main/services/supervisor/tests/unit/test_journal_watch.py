"""Event streams learn that their session changed from one watcher per process: one
query a round for every session followed, and on Postgres a notification on commit,
instead of each stream polling the database four times a second."""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator

import pytest
from sqlalchemy import event, text, update

from supervisor.agent.events import MessageEnd
from supervisor.api.events import SSE_END, iter_journal_sse
from supervisor.db.models import SessionRow
from supervisor.journal.partials import PartialOutputs
from supervisor.journal.reader import JournalReader
from supervisor.journal.watch import JournalWatch, Seen
from supervisor.journal.writer import JournalWriter
from tests.support.journal_db import Factory

pytestmark = pytest.mark.asyncio


async def _say(writer: JournalWriter, session_id: str, text: str) -> None:
    content = [{"type": "text", "text": text}]
    await writer.append(session_id, MessageEnd(role="user", content=content), turn_id="r1", trace_id=None)


def _count_queries(factory: Factory) -> list[str]:
    seen: list[str] = []
    engine = factory.kw["bind"].sync_engine

    @event.listens_for(engine, "before_cursor_execute")
    def _count(conn, cursor, statement, parameters, context, executemany):  # type: ignore[no-untyped-def]
        seen.append(statement)

    return seen


async def _ended(factory: Factory) -> None:
    async with factory() as db, db.begin():
        await db.execute(update(SessionRow).values(status="terminated"))


async def _stream(
    reader: JournalReader, watch: JournalWatch, session_id: str, *, partials: PartialOutputs | None = None
) -> AsyncIterator[str]:
    async def ended() -> bool:  # stands for the route's status check; the watch has it
        raise AssertionError("a watched stream reads its session's status from the watch")

    async for frame in iter_journal_sse(
        session_id=session_id, cursor=-1, reader=reader, watch=watch, has_ended=ended,
        heartbeat_secs=30, partials=partials,
    ):
        yield frame


async def test_many_streams_cost_one_query_a_round(factory: Factory) -> None:
    writer, reader = JournalWriter(factory), JournalReader(factory)
    sessions = [f"s{i}" for i in range(20)]
    for sid in sessions:
        await writer.ensure_session(sid)
    watch = JournalWatch(factory, poll_secs=0.05, listen=False)
    streams = [_stream(reader, watch, sid) for sid in sessions for _ in range(10)]  # 200 streams
    waits = [asyncio.ensure_future(s.__anext__()) for s in streams]
    await asyncio.sleep(0.3)  # all waiting on the watch
    queries = _count_queries(factory)
    await asyncio.sleep(0.5)  # ~10 rounds
    assert not any(w.done() for w in waits)
    assert 5 <= len(queries) <= 15, f"{len(queries)} queries in 10 rounds for 200 streams"
    await _say(writer, "s3", "hello")
    woken = await asyncio.wait_for(asyncio.gather(*waits[30:40]), timeout=2)
    assert all(f.startswith("id: 0\n") for f in woken)
    assert not any(w.done() for w in waits[:30] + waits[40:])
    for w in waits:
        w.cancel()
    await asyncio.gather(*waits, return_exceptions=True)
    await watch.close()


async def test_a_watched_stream_sends_events_partials_then_ends(factory: Factory) -> None:
    writer, reader = JournalWriter(factory), JournalReader(factory)
    await writer.ensure_session("s1")
    await _say(writer, "s1", "earlier")
    partials = PartialOutputs(factory)
    watch = JournalWatch(factory, poll_secs=0.02, listen=False)
    frames: list[str] = []

    async def collect() -> None:
        async for frame in _stream(reader, watch, "s1", partials=partials):
            frames.append(frame)

    task = asyncio.ensure_future(collect())
    await asyncio.sleep(0.2)
    await partials.put("s1", "r1", 0, "Hel", "")
    await asyncio.sleep(0.2)
    await partials.put("s1", "r1", 0, "Hello", "")
    await asyncio.sleep(0.2)
    await partials.clear("s1")
    await _say(writer, "s1", "Hello")
    await asyncio.sleep(0.2)
    await _ended(factory)
    await asyncio.wait_for(task, timeout=3)
    kinds = [f.split("\n")[0] if f.startswith(("id:", "event:")) else "partial" for f in frames]
    assert kinds == ["id: 0", "partial", "partial", "id: 1", "event: end"]
    assert frames[-1] == SSE_END
    await watch.close()


async def test_an_erased_session_ends_its_stream(factory: Factory) -> None:
    reader = JournalReader(factory)
    watch = JournalWatch(factory, poll_secs=0.02, listen=False)
    frames = [f async for f in _stream(reader, watch, "never-there")]
    assert frames == [SSE_END]
    await watch.close()


async def test_a_quiet_stream_pings(factory: Factory) -> None:
    writer, reader = JournalWriter(factory), JournalReader(factory)
    await writer.ensure_session("s1")
    watch = JournalWatch(factory, poll_secs=0.02, listen=False)
    stream = iter_journal_sse(session_id="s1", cursor=-1, reader=reader, watch=watch, heartbeat_secs=0.1)
    assert await asyncio.wait_for(stream.__anext__(), timeout=2) == ": ping\n\n"
    await stream.aclose()  # type: ignore[attr-defined]  # an async generator
    await watch.close()


async def test_on_postgres_a_write_wakes_its_streams_at_once(factory: Factory) -> None:
    if factory.kw["bind"].dialect.name != "postgresql":
        pytest.skip("notifications are Postgres's")
    writer = JournalWriter(factory)
    await writer.ensure_session("s1")
    watch = JournalWatch(factory, poll_secs=60, backstop_secs=60)  # only a notification wakes it
    seen = await watch.changed("s1", None, timeout=5)
    assert watch.listening and seen == Seen(-1, "active", None)
    waiting = asyncio.ensure_future(watch.changed("s1", seen, timeout=30))
    await asyncio.sleep(0.2)
    started = time.monotonic()
    await _say(writer, "s1", "hello")
    now = await asyncio.wait_for(waiting, timeout=5)
    assert now.last_seq == 0 and time.monotonic() - started < 2

    await PartialOutputs(factory).put("s1", "r1", 0, "Hi", "")
    now = await asyncio.wait_for(watch.changed("s1", now, timeout=30), timeout=5)
    assert now.partial_ms is not None
    await watch.close()
    assert not watch.listening


async def test_when_its_connection_drops_it_polls_until_it_listens_again(factory: Factory) -> None:
    if factory.kw["bind"].dialect.name != "postgresql":
        pytest.skip("notifications are Postgres's")
    writer = JournalWriter(factory)
    await writer.ensure_session("s1")
    watch = JournalWatch(factory, poll_secs=0.05, backstop_secs=0.2)
    seen = await watch.changed("s1", None, timeout=5)
    await asyncio.sleep(0.1)
    assert watch.listening
    async with factory() as db:  # the server ends the listening connection
        await db.execute(
            text(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = current_database()"
                " AND pid <> pg_backend_pid() AND (query LIKE 'LISTEN%' OR query = 'SELECT 1')"
            )
        )
    await asyncio.sleep(0.5)
    assert not watch.listening, "noticed within a round"
    waiting = asyncio.ensure_future(watch.changed("s1", seen, timeout=30))
    await asyncio.sleep(0.1)
    await _say(writer, "s1", "still seen")
    assert (await asyncio.wait_for(waiting, timeout=5)).last_seq == 0
    await watch.close()
