"""Time to first token, from journal events (any process recorded them)."""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

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
    tmp_path: Path, supervisor_db: async_sessionmaker[AsyncSession]
) -> None:
    # The Postgres test database is shared: unique ids, and the newest timestamps,
    # so the most recent window is exactly this test's runs.
    writer = JournalWriter(supervisor_db, blob_dir=tmp_path)
    t0 = 4_000_000_000_000 + uuid4().int % 1_000_000
    async with supervisor_db() as db:
        for delay in (100, 200, 300, 400, 5_000):
            sid = uuid4().hex
            await writer.ensure_session(sid)
            for seq, (kind, payload, ts) in enumerate(
                [
                    ("RunStart", {}, t0),
                    ("ToolStart", {"tool_name": "ls"}, t0 + 1),
                    ("MessageEnd", {"role": "assistant"}, t0 + delay),
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
        got = await recent_ttft(db, events=10)  # 5 runs: a start and an answer each
    assert (got.runs, got.p50_ms, got.p95_ms) == (5, 300, 5_000)
