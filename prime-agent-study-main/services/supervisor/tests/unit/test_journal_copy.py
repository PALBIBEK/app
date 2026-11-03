"""A fork copies its source's journal a batch per transaction (it was one transaction per
event, about 6 ms each), and a retried or racing copy carries on without duplicating."""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import event, func, select

from supervisor.agent.events import AgentEvent, MessageEnd, RunEnd
from supervisor.db.models import EventRow
from supervisor.journal import snapshots
from supervisor.journal.reader import JournalReader
from supervisor.journal.writer import JournalWriter
from tests.support.journal_db import Factory


def _journal(runs: int, per_run: int) -> list[tuple[AgentEvent, str | None, str | None, str]]:
    out: list[tuple[AgentEvent, str | None, str | None, str]] = []
    messages: list[dict] = []
    for r in range(runs):
        for i in range(per_run):
            reply = MessageEnd(role="assistant", content=[{"type": "text", "text": f"{r}.{i}"}])
            out.append((reply, f"r{r}", None, f"fork:{r}.{i}"))
        messages = [*messages, {"role": "assistant", "content": f"run {r}"}]
        out.append((RunEnd(messages=messages), f"r{r}", None, f"fork:{r}.end"))
    return out


async def _rows(factory: Factory, session_id: str) -> list[EventRow]:
    async with factory() as db:
        return list(
            (await db.execute(select(EventRow).where(EventRow.session_id == session_id).order_by(EventRow.seq)))
            .scalars()
            .all()
        )


@pytest.mark.asyncio
async def test_a_copy_writes_a_run_per_transaction(factory: Factory) -> None:
    writer, reader = JournalWriter(factory), JournalReader(factory)
    await writer.ensure_session("dst")
    events = _journal(runs=6, per_run=20)
    commits: list[int] = []
    engine = factory.kw["bind"].sync_engine

    def counted(conn: object) -> None:
        commits.append(1)

    event.listen(engine, "commit", counted)
    try:
        assert await writer.append_missing("dst", events) == len(events)
    finally:
        event.remove(engine, "commit", counted)
    assert 6 <= len(commits) <= 6 + 2, f"{len(commits)} commits for 6 runs"
    copied = await reader.read("dst")
    assert [e.payload for e in copied] == [e for e, *_ in events]
    run_ends = [r for r in await _rows(factory, "dst") if r.type == "RunEnd"]
    assert sum(snapshots.is_delta(r.payload) for r in run_ends) == 5, "each run's end a delta on the last"


@pytest.mark.asyncio
async def test_a_retried_copy_carries_on_where_the_journal_is(factory: Factory) -> None:
    writer, reader = JournalWriter(factory), JournalReader(factory)
    await writer.ensure_session("dst")
    events = _journal(runs=4, per_run=5)
    assert await writer.append_missing("dst", events[:13]) == 13  # the first attempt stopped
    # The source grew meanwhile: the retry copies more than the first attempt saw.
    assert await writer.append_missing("dst", events) == len(events) - 13
    assert await writer.append_missing("dst", events) == 0
    assert [e.payload for e in await reader.read("dst")] == [e for e, *_ in events]


@pytest.mark.asyncio
async def test_two_attempts_at_once_copy_each_event_once(factory: Factory) -> None:
    writer = JournalWriter(factory)
    await writer.ensure_session("dst")
    events = _journal(runs=5, per_run=8)
    small = JournalWriter(factory)
    done = await asyncio.gather(
        writer.append_missing("dst", events), small.append_missing("dst", events, batch=3)
    )
    assert sum(done) == len(events)
    async with factory() as db:
        count = await db.scalar(select(func.count()).select_from(EventRow).where(EventRow.session_id == "dst"))
    assert count == len(events)
    assert [e.payload for e in await JournalReader(factory).read("dst")] == [e for e, *_ in events]
