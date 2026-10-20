"""memory-contract — compaction/recall events + serde (R/G/R)."""

from __future__ import annotations

from pathlib import Path

from supervisor.agent.events import (
    CompactionEnd,
    CompactionStart,
    Envelope,
    MemoryRecall,
)
from supervisor.api.wire import envelope_to_wire
from supervisor.journal.serde import decode_event, encode_event


def test_mc_u1_serde_round_trip() -> None:
    start = CompactionStart(
        compaction_id="cmp-1",
        reason="threshold",
        tokens_before=120_000,
        first_kept_entry_id="entry-42",
    )
    end = CompactionEnd(
        compaction_id="cmp-1",
        stop_reason="ok",
        tokens_after=18_000,
        summary_digest="sha256:abc",
    )
    recall = MemoryRecall(
        block_id="blk-1",
        source="semantic",
        token_estimate=512,
        injected=True,
    )
    for ev in (start, end, recall):
        raw = encode_event(ev)
        back = decode_event(raw)
        assert back == ev


def test_mc_u2_wire_snake_case() -> None:
    env = Envelope(
        session_id="s1",
        seq=1,
        event_id="e1",
        ts_ms=1,
        turn_id=None,
        trace_id=None,
        payload=CompactionStart(
            compaction_id="cmp-9",
            reason="manual",
            tokens_before=50_000,
        ),
    )
    wire = envelope_to_wire(env)
    assert wire.type == "compaction_start"
    assert wire.data["compaction_id"] == "cmp-9"
    assert wire.data["reason"] == "manual"


def test_mc_u3_compaction_start_end_share_id() -> None:
    cid = "cmp-shared"
    start = CompactionStart(
        compaction_id=cid,
        reason="overflow",
        tokens_before=200_000,
    )
    end = CompactionEnd(compaction_id=cid, stop_reason="ok", tokens_after=30_000)
    assert start.compaction_id == end.compaction_id == cid


def test_mc_a1_session_journal_api_no_memory_provider_concretes() -> None:
    src = Path(__file__).resolve().parents[2] / "supervisor"
    banned = (
        "NullContextMemoryProvider",
        "BasicCompactionProvider",
    )
    for pkg in ("journal", "session", "api"):
        for path in (src / pkg).rglob("*.py"):
            text = path.read_text()
            for needle in banned:
                assert needle not in text, f"{path}: {needle}"
