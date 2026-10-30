"""The stream of a session that has ended ends too, after its last event.

It used to poll the finished session's journal four times a second for as long as anyone
kept the page open (and a client reconnected whenever the stream dropped).
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from supervisor.agent.events import MessageEnd
from supervisor.api.events import SSE_END, iter_journal_sse
from supervisor.db.engine import open_db
from supervisor.journal.reader import JournalReader
from supervisor.journal.writer import JournalWriter

pytestmark = pytest.mark.asyncio

SID = "s-ends"
FAST = {"poll_secs": 0.01, "ended_check_secs": 0.01}


@pytest.fixture
async def journal(tmp_path: Path) -> AsyncIterator[tuple[JournalWriter, JournalReader]]:
    async with open_db(f"sqlite+aiosqlite:///{tmp_path / 'stream.db'}") as (_, factory):
        writer = JournalWriter(factory)
        await writer.ensure_session(SID, tenant_id="t1")
        yield writer, JournalReader(factory)


async def _say(writer: JournalWriter, text: str) -> None:
    await writer.append(SID, MessageEnd(role="user", content=[{"type": "text", "text": text}]), turn_id="r1", trace_id=None)


async def test_an_ended_session_streams_its_events_then_ends(journal: tuple[JournalWriter, JournalReader]) -> None:
    writer, reader = journal
    await _say(writer, "one")
    await _say(writer, "two")

    async def ended() -> bool:
        return True

    frames = [f async for f in iter_journal_sse(session_id=SID, cursor=-1, reader=reader, has_ended=ended, **FAST)]
    assert [f.split("\n")[0] for f in frames] == ["id: 0", "id: 1", "event: end"]
    assert frames[-1] == SSE_END


async def test_what_was_written_as_it_ended_is_sent_before_the_end(
    journal: tuple[JournalWriter, JournalReader],
) -> None:
    writer, reader = journal
    checks = 0

    async def ended() -> bool:
        nonlocal checks
        checks += 1
        if checks == 2:  # the session's last words land just before it is seen ended
            await _say(writer, "goodbye")
            return True
        return False

    frames = [f async for f in iter_journal_sse(session_id=SID, cursor=-1, reader=reader, has_ended=ended, **FAST)]
    assert frames[0].startswith("id: 0") and "goodbye" in frames[0]
    assert frames[1:] == [SSE_END]


async def test_a_live_session_keeps_its_stream(journal: tuple[JournalWriter, JournalReader]) -> None:
    _, reader = journal

    async def ended() -> bool:
        return False

    stream = iter_journal_sse(session_id=SID, cursor=-1, reader=reader, has_ended=ended, heartbeat_secs=0.05, **FAST)
    first = await asyncio.wait_for(anext(stream), timeout=5)
    assert first == ": ping\n\n"
    await stream.aclose()


async def test_a_client_that_went_away_stops_the_polling(journal: tuple[JournalWriter, JournalReader]) -> None:
    _, reader = journal
    asked = 0

    async def gone() -> bool:
        nonlocal asked
        asked += 1
        return asked > 3

    frames = [f async for f in iter_journal_sse(session_id=SID, cursor=-1, reader=reader, is_disconnected=gone, **FAST)]
    assert frames == [] and asked == 4  # it stopped, without waiting for a heartbeat
