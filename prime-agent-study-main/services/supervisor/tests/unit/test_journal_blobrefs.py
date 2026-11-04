"""The journal records the blobs it keeps and the events that name them: erasing finds the
ones no one uses without scanning every journal, and blobs a failed write left behind are
swept once they are old enough."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import event, select, update

from supervisor.agent.events import HandLost, MessageEnd, ToolEnd
from supervisor.db.models import JournalBlobRefRow, JournalBlobRow
from supervisor.journal import blobrefs
from supervisor.journal.blobrefs import BlobSweeper
from supervisor.journal.blobs import LocalBlobs, blob_ref
from supervisor.journal.writer import JournalWriter
from supervisor.session.erase import SessionEraser
from tests.support.journal_db import Factory

BIG = "x" * 100_000


def _big(text: str = BIG) -> ToolEnd:
    return ToolEnd(tool_call_id="c", tool_name="read", result={"ok": True, "payload": text})


async def _refs(factory: Factory) -> set[tuple[str, str, int]]:
    async with factory() as db:
        rows = (await db.execute(select(JournalBlobRefRow))).scalars().all()
    return {(r.ref, r.session_id, int(r.seq)) for r in rows}


async def _kept(factory: Factory) -> set[str]:
    async with factory() as db:
        return set(await db.scalars(select(JournalBlobRow.ref)))


@pytest.mark.asyncio
async def test_each_event_records_the_blobs_it_names(factory: Factory, tmp_path: Path) -> None:
    blobs = LocalBlobs(tmp_path / "b")
    writer = JournalWriter(factory, blobs=blobs)
    await writer.ensure_session("s1")
    await writer.append("s1", _big(), turn_id="r1", trace_id=None)
    logs = await writer.put_blob(b"exit 137")
    assert logs is not None
    await writer.append("s1", HandLost(hand_id="h", provider="p", reason="gone", logs_ref=logs), turn_id=None, trace_id=None)
    await writer.append("s1", MessageEnd(role="user", content=[]), turn_id="r1", trace_id=None)
    refs = await _refs(factory)
    assert {(seq, ref == logs) for ref, _, seq in refs} == {(0, False), (1, True)}
    assert {ref for ref, _, _ in refs} == await _kept(factory)


@pytest.mark.asyncio
async def test_erasing_deletes_only_unused_blobs_without_reading_payloads(factory: Factory, tmp_path: Path) -> None:
    blobs = LocalBlobs(tmp_path / "b")
    writer = JournalWriter(factory, blobs=blobs)
    for sid in ("s1", "s2"):
        await writer.ensure_session(sid)
    await writer.append("s1", _big(), turn_id="r1", trace_id=None)  # the same bytes in both
    await writer.append("s2", _big(), turn_id="r1", trace_id=None)
    await writer.append("s1", _big("y" * 100_000), turn_id="r1", trace_id=None)  # s1's own
    statements: list[str] = []
    engine = factory.kw["bind"].sync_engine

    def seen(conn: object, cursor: object, statement: str, *args: object) -> None:
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", seen)
    try:
        erased = await SessionEraser(factory, blobs=blobs, objects=None).erase("s1")
    finally:
        event.remove(engine, "before_cursor_execute", seen)
    assert erased.payloads == 1, "the shared blob stays: s2 still names it"
    assert len(list((tmp_path / "b").iterdir())) == 1
    assert {s for _, s, _ in await _refs(factory)} == {"s2"}
    scans = [s for s in statements if "events" in s and "payload" in s and "events.session_id =" not in s]
    assert not scans, "no read of other sessions' payloads (it scanned every journal for blob refs)"


@pytest.mark.asyncio
async def test_the_sweep_deletes_old_blobs_no_event_names(factory: Factory, tmp_path: Path) -> None:
    blobs = LocalBlobs(tmp_path / "b")
    writer = JournalWriter(factory, blobs=blobs)
    await writer.ensure_session("s1")
    await writer.append("s1", _big(), turn_id="r1", trace_id=None)  # named
    orphan = await writer.put_blob(b"stored, then its write failed")
    fresh = await writer.put_blob(b"stored a moment ago, its write on its way")
    assert orphan and fresh
    async with factory() as db, db.begin():
        await db.execute(
            update(JournalBlobRow)
            .where(JournalBlobRow.ref != fresh)
            .values(stored_at=datetime.now(UTC) - timedelta(days=2))
        )
    assert await BlobSweeper(factory, blobs).sweep() == 1
    assert not (tmp_path / "b" / orphan).exists()
    assert (tmp_path / "b" / fresh).exists(), "within the grace: a write may still name it"
    assert len(await _kept(factory)) == 2
    assert await BlobSweeper(factory, blobs).sweep() == 0


@pytest.mark.asyncio
async def test_a_writer_storing_the_same_bytes_during_a_sweep_keeps_them(factory: Factory, tmp_path: Path) -> None:
    store = LocalBlobs(tmp_path / "b")
    data = b"the same bytes, stored again"
    ref = blob_ref(data)
    writer = JournalWriter(factory, blobs=store)

    class Racing(LocalBlobs):
        async def delete(self, ref_: str) -> bool:
            gone = await super().delete(ref_)
            await writer.put_blob(data)  # a writer records and stores it right then
            return gone

    racing = Racing(tmp_path / "b")
    await store.put(ref, data)
    await blobrefs.kept(factory, ref)
    async with factory() as db, db.begin():
        await db.execute(update(JournalBlobRow).values(stored_at=datetime.now(UTC) - timedelta(days=2)))
    await BlobSweeper(factory, racing).sweep()
    assert await store.get(ref) == data, "put back: a writer recorded it again while it was deleted"
    assert ref in await _kept(factory)
