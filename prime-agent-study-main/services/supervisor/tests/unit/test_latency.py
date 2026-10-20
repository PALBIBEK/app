"""Time to first token, from journal events (any process recorded them)."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from supervisor.db.models import EventRow
from supervisor.journal.writer import JournalWriter
from supervisor.obs.latency import recent_ttft, ttfts


def test_ttft_is_start_to_first_assistant_answer_per_run() -> None:
    rows = [
        ("s1", "r1", "RunStart", {}, 1_000),
        ("s1", "r1", "MessageEnd", {"role": "user"}, 1_005),
        ("s1", "r1", "MessageEnd", {"role": "assistant"}, 1_400),
        ("s1", "r1", "MessageEnd", {"role": "assistant"}, 9_000),  # a later step: not first
        ("s2", "r9", "RunStart", {}, 2_000),
        ("s2", "r9", "MessageEnd", {"role": "assistant"}, 2_100),
        ("s3", "r5", "RunStart", {}, 3_000),  # still waiting: no answer yet
        ("s4", None, "MessageEnd", {"role": "assistant"}, 10),  # outside any run
    ]
    assert sorted(ttfts(rows)) == [100, 400]


async def test_recent_ttft_reads_the_journal(
    tmp_path: Path, memory_db: async_sessionmaker[AsyncSession]
) -> None:
    writer = JournalWriter(memory_db, blob_dir=tmp_path)
    async with memory_db() as db:
        for i, delay in enumerate((100, 200, 300, 400, 5_000)):
            sid = f"s{i}"
            await writer.ensure_session(sid)
            for seq, (kind, payload, ts) in enumerate(
                [
                    ("RunStart", {}, 1_000),
                    ("ToolStart", {"tool_name": "ls"}, 1_001),
                    ("MessageEnd", {"role": "assistant"}, 1_000 + delay),
                ]
            ):
                db.add(
                    EventRow(
                        session_id=sid,
                        seq=seq,
                        event_id=f"{sid}-{seq}",
                        turn_id="r",
                        type=kind,
                        payload=payload,
                        ts_ms=ts,
                    )
                )
        await db.commit()
        got = await recent_ttft(db)
    assert (got.runs, got.p50_ms, got.p95_ms) == (5, 300, 5_000)
