"""A client that starts from the beginning of a long session gets its events a page at a
time: the stream used to read the whole journal at once and hold it in memory."""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import update

from supervisor.agent.events import MessageEnd
from supervisor.api import events as events_api
from supervisor.api.events import PAGE, SSE_END, iter_journal_sse
from supervisor.db.models import SessionRow
from supervisor.journal.reader import JournalReader
from supervisor.journal.watch import JournalWatch
from supervisor.journal.writer import JournalWriter
from tests.support.journal_db import Factory

N = 2 * PAGE + 200


class _Counting(JournalReader):
    def __init__(self, factory: Factory) -> None:
        super().__init__(factory)
        self.sizes: list[int] = []

    async def read(self, session_id: str, *, since: int = -1, limit: int | None = None) -> Any:
        found = await super().read(session_id, since=since, limit=limit)
        self.sizes.append(len(found))
        return found


async def _long_session(factory: Factory) -> None:
    writer = JournalWriter(factory)
    await writer.ensure_session("s1")
    said = MessageEnd(role="assistant", content=[{"type": "text", "text": "x"}])
    for _ in range(N // 100):
        await writer.append_batch("s1", [(said, "r1", None)] * 100)
    async with factory() as db, db.begin():  # ended: a watched stream ends after it all
        await db.execute(update(SessionRow).values(status="terminated"))


async def _ended() -> bool:
    return True


@pytest.mark.asyncio
@pytest.mark.parametrize("watched", [False, True], ids=["polling", "watched"])
async def test_a_long_session_is_sent_a_page_at_a_time(factory: Factory, watched: bool) -> None:
    await _long_session(factory)
    reader = _Counting(factory)
    watch = JournalWatch(factory, poll_secs=0.02) if watched else None
    frames = [
        f
        async for f in iter_journal_sse(
            session_id="s1", cursor=-1, reader=reader, has_ended=_ended, watch=watch,
            poll_secs=0.01, ended_check_secs=0.01,
        )
    ]
    if watch is not None:
        await watch.close()
    ids = [int(f.split("\n")[0].removeprefix("id: ")) for f in frames if f.startswith("id: ")]
    assert ids == list(range(N)) and frames[-1] == SSE_END
    assert max(reader.sizes) == PAGE, "never more than a page held at once"


@pytest.mark.asyncio
async def test_a_reader_page_starts_after_the_last_seq(factory: Factory) -> None:
    await _long_session(factory)
    reader = JournalReader(factory)
    first = await reader.read("s1", limit=3)
    second = await reader.read("s1", since=first[-1].seq, limit=3)
    assert [e.seq for e in first + second] == [0, 1, 2, 3, 4, 5]


def test_the_page_is_what_the_stream_reads() -> None:
    assert events_api.PAGE == 500
