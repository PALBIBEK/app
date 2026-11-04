"""The scrub masks what was journalled before the journal masked what it stores: event
payloads, large payloads and sandbox logs in the blob store, the tool outbox and titles."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from sqlalchemy import select

from supervisor import scrub_cli
from supervisor.agent.events import HandLost, MessageEnd, RunEnd, ToolEnd
from supervisor.db.models import EventRow, JournalBlobRefRow, SessionIndexRow, ToolOutboxRow
from supervisor.journal import snapshots
from supervisor.journal.blobs import LocalBlobs, blob_ref
from supervisor.journal.reader import JournalReader
from supervisor.journal.scrub import scrub
from supervisor.journal.writer import JournalWriter
from tests.support.journal_db import Factory

KEY = "sk-or-v1-" + "0a1b2c3d" * 8
MASKED = "[redacted:openrouter_key]"


async def _as_before(factory: Factory, blobs: LocalBlobs, monkeypatch: pytest.MonkeyPatch) -> JournalWriter:
    """A journal as a build from before masking wrote it."""
    monkeypatch.setattr("supervisor.journal.writer.mask", lambda value: value)
    writer = JournalWriter(factory, blobs=blobs, blob_threshold=2048)
    await writer.ensure_session("s1")
    await writer.append("s1", MessageEnd(role="user", content=[{"type": "text", "text": f"use {KEY}"}]), turn_id="r1", trace_id=None)
    big = {"stdout": "x" * 4000 + f"\nOPENROUTER_API_KEY={KEY}\n"}
    await writer.append("s1", ToolEnd(tool_call_id="c", tool_name="bash", result=big), turn_id="r1", trace_id=None)
    first = [{"role": "user", "content": f"use {KEY}"}]
    await writer.append("s1", RunEnd(messages=first), turn_id="r1", trace_id=None)
    await writer.append("s1", RunEnd(messages=[*first, {"role": "assistant", "content": f"ok {KEY}"}]), turn_id="r2", trace_id=None)
    logs = f"env\nOPENROUTER_API_KEY={KEY}\n".encode()
    ref = blob_ref(logs)
    await blobs.put(ref, logs)
    await writer.append("s1", HandLost(hand_id="h", provider="docker", reason="gone", logs_ref=ref, logs_bytes=len(logs)), turn_id=None, trace_id=None)
    async with factory() as db, db.begin():
        db.add(ToolOutboxRow(id="o1", session_id="s1", tool_call_id="c", idempotency_key="s1:c", tool_name="bash",
                             arguments={"cmd": f"curl -H 'Authorization: Bearer {KEY}'"}, status="done", result={"ok": True}))
        (await db.get(SessionIndexRow, "s1")).title = f"key {KEY}"  # type: ignore[union-attr]
    monkeypatch.undo()
    return writer


async def _everything(factory: Factory, root: Path) -> str:
    async with factory() as db:
        rows = [json.dumps(p) for p in (await db.execute(select(EventRow.payload))).scalars()]
        rows += [json.dumps(r.arguments) for r in (await db.execute(select(ToolOutboxRow))).scalars()]
        rows += [str(i.title) for i in (await db.execute(select(SessionIndexRow))).scalars()]
    rows += [p.read_bytes().decode(errors="replace") for p in root.iterdir() if p.is_file()]
    return "\n".join(rows)


@pytest.mark.asyncio
async def test_a_dry_run_counts_and_changes_nothing(factory: Factory, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    blobs = LocalBlobs(tmp_path / "blobs")
    await _as_before(factory, blobs, monkeypatch)
    before = await _everything(factory, tmp_path / "blobs")
    report = await scrub(factory, blobs)
    assert (report.events_masked, report.blobs_masked, report.outbox_masked, report.titles_masked) == (5, 2, 1, 1)
    assert await _everything(factory, tmp_path / "blobs") == before


@pytest.mark.asyncio
async def test_it_masks_every_stored_copy_and_the_session_still_reads(
    factory: Factory, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    blobs = LocalBlobs(tmp_path / "blobs")
    await _as_before(factory, blobs, monkeypatch)
    assert KEY in await _everything(factory, tmp_path / "blobs")
    report = await scrub(factory, blobs, apply=True, batch=2)
    assert report.blobs_deleted == 2, "the unmasked blobs are gone"
    stored = await _everything(factory, tmp_path / "blobs")
    assert KEY not in stored and MASKED in stored

    reader = JournalReader(factory, blobs=blobs)
    events = await reader.read("s1")
    assert KEY not in str([e.payload for e in events])
    assert await reader.latest_history("s1") == [
        {"role": "user", "content": f"use {MASKED}"},
        {"role": "assistant", "content": f"ok {MASKED}"},
    ], "a delta and its base, masked apart, still make the masked conversation"
    async with factory() as db:
        run_ends = (await db.execute(select(EventRow.payload).where(EventRow.type == "RunEnd").order_by(EventRow.seq))).scalars().all()
    assert snapshots.is_delta(run_ends[1])
    lost = next(e.payload for e in events if isinstance(e.payload, HandLost))
    logs = await reader.hand_logs("s1", lost.logs_ref or "")
    assert logs is not None and MASKED.encode() in logs and lost.logs_bytes == len(logs)

    async with factory() as db:
        named = set(await db.scalars(select(JournalBlobRefRow.ref)))
    assert named == set(report.replaced.values()), "the events name the masked blobs, and only those"

    again = await scrub(factory, blobs, apply=True)
    assert (again.events_masked, again.blobs_masked, again.outbox_masked) == (0, 0, 0), "a second pass finds nothing"


@pytest.mark.asyncio
async def test_a_stopped_run_carries_on_from_where_it_was(
    factory: Factory, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    blobs = LocalBlobs(tmp_path / "blobs")
    await _as_before(factory, blobs, monkeypatch)
    seen: list[tuple[str, int] | None] = []
    first = await scrub(factory, blobs, apply=True, batch=2, progress=lambda r: seen.append(r.position))
    assert seen[0] == ("s1", 1)
    assert first.events == 5
    # A run told to start after the first batch reads only the rest.
    rest = await scrub(factory, blobs, after=("s1", 1))
    assert rest.events == 3 and rest.blobs_deleted == 0


def test_the_cli_says_what_a_dry_run_would_do(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        scrub_cli.main(["--help"])
    assert "--apply" in capsys.readouterr().out
