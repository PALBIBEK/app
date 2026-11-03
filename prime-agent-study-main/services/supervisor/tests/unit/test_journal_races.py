"""The journal under the races production has, on SQLite and on Postgres (where writers
really run at once: set SUPERVISOR_TEST_PG_URL).

Temporal can start a step's next attempt while the last one still runs, so two writers can
journal the same keyed event, create the same session or copy the same fork at once. Each
must land once.
"""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import select

from supervisor.agent.events import MessageEnd, RunStart, ToolEnd
from supervisor.db.models import EventRow, SessionIndexRow, SessionRow
from supervisor.journal.reader import JournalReader
from supervisor.journal.writer import JournalWriter, keyed_event_id
from tests.support.journal_db import Factory

pytestmark = pytest.mark.asyncio

ROUNDS = 10


async def _rows(factory: Factory, session_id: str) -> list[EventRow]:
    async with factory() as db:
        return list(
            (await db.scalars(select(EventRow).where(EventRow.session_id == session_id).order_by(EventRow.seq))).all()
        )


def _end(text_: str = "done") -> ToolEnd:
    return ToolEnd(tool_call_id="c1", tool_name="bash", result={"ok": True, "payload": text_})


async def test_a_keyed_write_raced_by_a_retry_lands_once(factory: Factory) -> None:
    w = JournalWriter(factory)
    for i in range(ROUNDS):
        sid = f"s{i}"
        await w.ensure_session(sid)
        await w.append(sid, RunStart(), turn_id="r1", trace_id=None)
        # Two attempts of one model step, each with its own reply (each called the model).
        written = await asyncio.gather(
            *(
                w.write(sid, MessageEnd(role="assistant", content=[{"type": "text", "text": f"attempt {a}"}]),
                        turn_id="r1", trace_id=None, event_key="r1:0:assistant")
                for a in range(3)
            )
        )
        rows = await _rows(factory, sid)
        assert [r.type for r in rows] == ["RunStart", "MessageEnd"], "one reply, whichever won"
        assert sum(x.created for x in written) == 1
        assert {(x.seq, x.event_id, x.ts_ms) for x in written} == {(1, rows[1].event_id, rows[1].ts_ms)}, (
            "every attempt is told where the one reply is, as stored"
        )


async def test_a_retried_write_finds_the_event_as_stored(factory: Factory) -> None:
    w = JournalWriter(factory)
    await w.ensure_session("s1")
    first = await w.write("s1", _end(), turn_id="r1", trace_id=None, event_key="r1:c1:end")
    again = await w.write("s1", _end("a different result"), turn_id="r1", trace_id=None, event_key="r1:c1:end")
    assert first.created and not again.created
    assert (again.seq, again.event_id, again.ts_ms) == (first.seq, first.event_id, first.ts_ms)
    assert first.event_id == keyed_event_id("s1", "r1:c1:end")
    stored = (await JournalReader(factory).read("s1"))[0]
    assert stored.payload == _end(), "the first write stands"
    unkeyed = await w.write("s1", _end(), turn_id=None, trace_id=None)
    assert unkeyed.created and unkeyed.seq == 1 and (await _rows(factory, "s1"))[1].event_id == unkeyed.event_id


async def test_a_keyed_batch_raced_by_a_retry_lands_once(factory: Factory) -> None:
    w = JournalWriter(factory)
    await w.ensure_session("s1")
    batch = [(_end(str(i)), "r1", None) for i in range(3)]
    ids = [keyed_event_id("s1", f"fork:e{i}") for i in range(3)]
    last = await asyncio.gather(*(w.append_batch("s1", batch, event_ids=ids) for _ in range(3)))
    assert set(last) == {2}
    assert [r.event_id for r in await _rows(factory, "s1")] == ids


async def test_the_same_session_created_twice_at_once_is_created_once(factory: Factory) -> None:
    w = JournalWriter(factory)
    for i in range(ROUNDS):
        await asyncio.gather(*(w.ensure_session(f"s{i}", runtime="githi") for _ in range(3)))
    async with factory() as db:
        assert await db.scalar(select(SessionRow.id).where(SessionRow.id == "s0")) == "s0"
        assert len((await db.scalars(select(SessionIndexRow.session_id))).all()) == ROUNDS


async def test_writers_racing_on_one_session_leave_no_gap(factory: Factory) -> None:
    w = JournalWriter(factory)
    await w.ensure_session("s1")

    async def writer(k: int) -> None:
        for i in range(10):
            await w.write("s1", _end(), turn_id=None, trace_id=None, event_key=f"w{k}:{i % 5}")

    await asyncio.gather(*(writer(k) for k in range(6)))
    rows = await _rows(factory, "s1")
    assert [r.seq for r in rows] == list(range(30)), "6 writers x 5 keys, each once, no gap"


async def test_a_retried_record_is_published_once_as_stored(session_env: dict) -> None:
    """The session layer publishes what the journal holds (its id and time), and a retry
    that found its event journalled already publishes nothing (no duplicate live event or
    trace span)."""
    manager = session_env["manager"]
    sid = await manager.create(tenant_id="t1", harness_version="kg-v1", runtime_name="echo")
    sub = manager.subscribe_live(sid)
    while not sub.queue.empty():
        sub.queue.get_nowait()
    await asyncio.gather(
        *(manager._record(sid, _end(), turn_id="r1", trace_id=None, event_key="r1:c1:end") for _ in range(3))
    )
    published = [sub.queue.get_nowait() for _ in range(sub.queue.qsize())]
    (row,) = [r for r in await _rows(session_env["factory"], sid) if r.type == "ToolEnd"]
    assert [(e.seq, e.event_id, e.ts_ms) for e in published] == [(row.seq, row.event_id, row.ts_ms)]
