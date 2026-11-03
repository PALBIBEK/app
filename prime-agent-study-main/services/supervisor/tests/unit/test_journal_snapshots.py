"""A run's closing conversation is stored as what it added to the previous run's, and read
back whole; the next run's history is found without reading the whole journal."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select

from supervisor.agent.events import CompactionEnd, MemoryContext, MessageEnd, RunEnd, Usage
from supervisor.db.models import EventRow
from supervisor.journal import snapshots
from supervisor.journal.blobs import LocalBlobs
from supervisor.journal.reader import JournalReader
from supervisor.journal.serde import decode_event
from supervisor.journal.snapshots import DELTA_TYPE, MAX_CHAIN
from supervisor.journal.writer import JournalWriter
from supervisor.memory.turn import SessionMemory
from tests.support.journal_db import Factory


def _msg(i: int, role: str = "user") -> dict[str, Any]:
    return {"role": role, "content": [{"type": "text", "text": f"message {i}"}]}


async def _stored(factory: Factory, session_id: str) -> list[dict[str, Any]]:
    async with factory() as db:
        rows = (
            await db.execute(
                select(EventRow.payload)
                .where(EventRow.session_id == session_id, EventRow.type == "RunEnd")
                .order_by(EventRow.seq)
            )
        ).scalars().all()
    return list(rows)


async def _runs(writer: JournalWriter, session_id: str, n: int) -> list[list[dict[str, Any]]]:
    histories: list[list[dict[str, Any]]] = []
    messages: list[dict[str, Any]] = []
    for run in range(n):
        messages = [*messages, _msg(2 * run), _msg(2 * run + 1, "assistant")]
        await writer.append(session_id, MessageEnd(role="user", content=[], usage=None), turn_id=f"r{run}", trace_id=None)
        await writer.append(
            session_id, RunEnd(messages=messages, total_usage=Usage(1, 2)), turn_id=f"r{run}", trace_id=None
        )
        histories.append(messages)
    return histories


@pytest.mark.asyncio
async def test_each_run_stores_what_it_added_and_reads_back_whole(factory: Factory) -> None:
    writer, reader = JournalWriter(factory), JournalReader(factory)
    await writer.ensure_session("s1")
    histories = await _runs(writer, "s1", 2 * MAX_CHAIN + 3)

    stored = await _stored(factory, "s1")
    assert all(len(p["messages"]) <= 2 or p["_type"] == "RunEnd" for p in stored)
    full = [i for i, p in enumerate(stored) if not snapshots.is_delta(p)]
    assert full == [0, MAX_CHAIN + 1, 2 * MAX_CHAIN + 2], "a full one every MAX_CHAIN deltas"
    assert max(snapshots.depth(p) for p in stored) == MAX_CHAIN

    run_ends = [e.payload for e in await reader.read("s1") if isinstance(e.payload, RunEnd)]
    assert run_ends == [RunEnd(messages=h, total_usage=Usage(1, 2)) for h in histories]
    assert await reader.latest_history("s1") == histories[-1]
    for i, h in enumerate(histories):  # one run on its own, its bases not in what was read
        (event,) = [e for e in await reader.read_turn("s1", f"r{i}") if isinstance(e.payload, RunEnd)]
        assert isinstance(event.payload, RunEnd) and event.payload.messages == h


@pytest.mark.asyncio
async def test_storage_grows_with_what_is_said_not_with_runs_times_length(factory: Factory) -> None:
    writer = JournalWriter(factory)
    await writer.ensure_session("s1")
    await _runs(writer, "s1", 60)
    stored_messages = sum(len(p["messages"]) for p in await _stored(factory, "s1"))
    assert stored_messages < 60 * 2 * 5  # every run in full would be 60 * 61 = 3660


@pytest.mark.asyncio
async def test_a_rewritten_conversation_is_stored_in_full(factory: Factory) -> None:
    writer, reader = JournalWriter(factory), JournalReader(factory)
    await writer.ensure_session("s1")
    await _runs(writer, "s1", 3)
    summary = [{"role": "user", "content": "summary of the conversation so far"}]
    await writer.append("s1", RunEnd(messages=summary), turn_id="r9", trace_id=None)
    await writer.append("s1", RunEnd(messages=[*summary, _msg(99)]), turn_id="r10", trace_id=None)
    stored = await _stored(factory, "s1")
    assert not snapshots.is_delta(stored[3]) and snapshots.is_delta(stored[4])
    assert stored[4]["messages"] == [_msg(99)]
    assert await reader.latest_history("s1") == [*summary, _msg(99)]


@pytest.mark.asyncio
async def test_a_large_delta_is_kept_in_the_blob_store(factory: Factory, tmp_path: Path) -> None:
    blobs = LocalBlobs(tmp_path)
    writer, reader = JournalWriter(factory, blobs=blobs, blob_threshold=512), JournalReader(factory, blobs=blobs)
    await writer.ensure_session("s1")
    first = [_msg(0)]
    big = {"role": "tool", "content": "x" * 4096}
    await writer.append("s1", RunEnd(messages=first), turn_id="r0", trace_id=None)
    await writer.append("s1", RunEnd(messages=[*first, big]), turn_id="r1", trace_id=None)
    stored = await _stored(factory, "s1")
    assert "_blob_ref" in stored[1]
    assert snapshots.is_delta(json.loads(await blobs.get(stored[1]["_blob_ref"])))
    assert await reader.latest_history("s1") == [*first, big]


@pytest.mark.asyncio
async def test_a_delta_whose_base_is_not_the_previous_run_end_still_resolves(factory: Factory) -> None:
    """Two runs ending at once may both stand on the same older RunEnd."""
    writer, reader = JournalWriter(factory), JournalReader(factory)
    await writer.ensure_session("s1")
    histories = await _runs(writer, "s1", MAX_CHAIN)  # a chain MAX_CHAIN - 1 deep
    async with factory() as db:
        base = (await db.execute(select(EventRow).where(EventRow.type == "RunEnd").order_by(EventRow.seq))).scalars().first()
    assert base is not None
    for _ in range(MAX_CHAIN + 2):  # a long run of unrelated RunEnds after it
        await writer.append("s1", RunEnd(messages=[_msg(500)]), turn_id="x", trace_id=None)
    payload = snapshots.encode_run_end(
        {"_type": "RunEnd", "messages": [*histories[0], _msg(7)]}, snapshots.Snapshot(int(base.seq), 0, histories[0])
    )
    async with factory() as db, db.begin():
        db.add(EventRow(session_id="s1", seq=999, event_id="late", type="RunEnd", payload=payload, ts_ms=1))
    event = await reader.event("s1", "late")
    assert event is not None and event.payload == RunEnd(messages=[*histories[0], _msg(7)])
    assert await reader.latest_history("s1") == [*histories[0], _msg(7)]


def test_a_build_from_before_deltas_reads_one_as_unknown_not_as_a_shorter_conversation() -> None:
    delta = snapshots.encode_run_end(
        {"_type": "RunEnd", "messages": [_msg(0), _msg(1)]}, snapshots.Snapshot(3, 0, [_msg(0)])
    )
    assert delta["_type"] == DELTA_TYPE
    assert type(decode_event(delta)).__name__ == "UnknownEvent"


@pytest.mark.asyncio
async def test_a_fork_copy_stores_its_own_deltas(factory: Factory) -> None:
    """Copied events are re-encoded against the copy's own journal, not the source's seqs."""
    writer, reader = JournalWriter(factory), JournalReader(factory)
    await writer.ensure_session("src")
    await writer.ensure_session("dst")
    await writer.append("dst", MessageEnd(role="user", content=[], usage=None), turn_id=None, trace_id=None)
    histories = await _runs(writer, "src", 4)
    for event in await reader.read("src"):
        await writer.append("dst", event.payload, turn_id=event.turn_id, trace_id=None)
    assert await reader.latest_history("dst") == histories[-1]
    dst_seqs = {p.get("_base_seq") for p in await _stored(factory, "dst")} - {None}
    async with factory() as db:
        own = set((await db.execute(select(EventRow.seq).where(EventRow.session_id == "dst"))).scalars().all())
    assert dst_seqs <= own


@pytest.mark.asyncio
async def test_prior_history_reads_only_the_latest_checkpoints(factory: Factory, monkeypatch: pytest.MonkeyPatch) -> None:
    writer, reader = JournalWriter(factory), JournalReader(factory)
    await writer.ensure_session("s1")
    histories = await _runs(writer, "s1", 30)
    memory = SessionMemory.__new__(SessionMemory)
    memory._reader = reader

    async def no_full_read(*args: object, **kwargs: object) -> None:
        raise AssertionError("prior_history read the whole journal")

    monkeypatch.setattr(reader, "read", no_full_read)
    assert await memory.prior_history("s1") == histories[-1]

    compacted = [{"role": "user", "content": "compacted"}]
    await writer.append("s1", CompactionEnd(compaction_id="c", stop_reason="ok", messages=compacted), turn_id=None, trace_id=None)
    assert await memory.prior_history("s1") == compacted
    await writer.append("s1", CompactionEnd(compaction_id="d", stop_reason="error"), turn_id=None, trace_id=None)
    assert await memory.prior_history("s1") == compacted, "a failed compaction is not a checkpoint"
    for _ in range(10):  # more failed ones than one page holds
        await writer.append("s1", CompactionEnd(compaction_id="e", stop_reason="error"), turn_id=None, trace_id=None)
    assert await memory.prior_history("s1") == compacted

    last = (await reader.last_of("s1", ["CompactionEnd"]))[0].seq
    await writer.append("s1", MemoryContext("audience-b", last), turn_id=None, trace_id=None)
    assert await memory.prior_history("s1") == [], "the audience cutoff has passed every checkpoint"


def test_a_run_total_usage_reads_back_as_usage() -> None:
    """``traces`` reads ``total_usage.cost_total`` of a replayed RunEnd."""
    from supervisor.journal.serde import encode_event

    event = decode_event(encode_event(RunEnd(messages=[], total_usage=Usage(1, 2, cost_total=0.5))))
    assert isinstance(event, RunEnd) and event.total_usage == Usage(1, 2, cost_total=0.5)
