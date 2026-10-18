"""compaction-journal — MemoryOps + recovery for unpaired compaction_start (R/G/R)."""

from __future__ import annotations

from pathlib import Path

import pytest

from supervisor.agent.events import (
    CompactionEnd,
    CompactionStart,
    Envelope,
)
from supervisor.db.engine import open_db
from supervisor.journal.reader import JournalReader
from supervisor.journal.serde import decode_event, encode_event
from supervisor.journal.writer import JournalWriter
from supervisor.seams.compaction import SUMMARY_MARKER
from supervisor.seams.context_memory import (
    CompactionDecision,
    CompactionPreparation,
    CompactionResult,
    MemoryInvokeContext,
)
from supervisor.seams.providers.basic_compaction import BasicCompactionProvider
from supervisor.seams.providers.fake_gateway import FakeModelGatewayClient
from supervisor.seams.providers.null_context_memory import NullContextMemoryProvider
from supervisor.session.memory_ops import MemoryOps
from supervisor.session.recovery import Recovery, ResumeState


def _env(seq: int, payload, *, session_id: str = "sess-1") -> Envelope:
    return Envelope(
        session_id=session_id,
        seq=seq,
        event_id=f"e{seq}",
        ts_ms=1_700_000_000_000 + seq,
        turn_id=None,
        trace_id=None,
        payload=payload,
    )


def _prep() -> CompactionPreparation:
    return CompactionPreparation(
        first_kept_entry_id="entry-1",
        messages_to_summarize=[{"role": "user", "content": "long history"}],
        tokens_before=95_000,
        previous_summary=None,
        context_window=128_000,
        reserve_tokens=16_384,
    )


@pytest.fixture
async def journal(tmp_path: Path):
    async with open_db(f"sqlite+aiosqlite:///{tmp_path / 'j.db'}") as (_engine, factory):
        w = JournalWriter(factory, blob_dir=tmp_path / "blobs")
        r = JournalReader(factory, blob_dir=tmp_path / "blobs")
        await w.ensure_session("sess-1", runtime="echo")
        yield w, r


def test_cj_u1_unpaired_compaction_starts_helper() -> None:
    from supervisor.session.compaction_state import unpaired_compaction_starts

    envs = [
        _env(
            1,
            CompactionStart(
                compaction_id="cmp-orphan",
                reason="threshold",
                tokens_before=90_000,
            ),
        )
    ]
    unpaired = unpaired_compaction_starts(envs)
    assert len(unpaired) == 1
    assert unpaired[0].compaction_id == "cmp-orphan"


def test_cj_u2_paired_compaction_not_unpaired() -> None:
    from supervisor.session.compaction_state import unpaired_compaction_starts

    cid = "cmp-ok"
    envs = [
        _env(
            1,
            CompactionStart(compaction_id=cid, reason="manual", tokens_before=50_000),
        ),
        _env(2, CompactionEnd(compaction_id=cid, stop_reason="ok", tokens_after=10_000)),
    ]
    assert unpaired_compaction_starts(envs) == []


@pytest.mark.asyncio
async def test_cj_b1_compact_journals_start_end(journal) -> None:
    w, r = journal
    gateway = FakeModelGatewayClient(assistant_text="## Goal\ndone")
    ops = MemoryOps(
        session_id="sess-1",
        memory=BasicCompactionProvider(gateway),
        writer=w,
    )
    result = await ops.compact(_prep(), reason="threshold")
    assert result.stop_reason == "ok"
    assert result.summary is not None
    assert SUMMARY_MARKER in result.summary
    envs = await r.read("sess-1", since=-1)
    types = [type(e.payload).__name__ for e in envs]
    assert types == ["CompactionStart", "CompactionEnd"]
    start = envs[0].payload
    end = envs[1].payload
    assert isinstance(start, CompactionStart)
    assert isinstance(end, CompactionEnd)
    assert start.compaction_id == end.compaction_id
    assert end.stop_reason == "ok"
    assert end.tokens_after is not None
    assert end.summary_digest


@pytest.mark.asyncio
async def test_cj_b2_cancel_journals_cancelled_end(journal) -> None:
    w, r = journal

    class CancelMemory(NullContextMemoryProvider):
        def before_compact(self, ctx, preparation, *, reason):  # type: ignore[no-untyped-def]
            return CompactionDecision(action="cancel")

    ops = MemoryOps(session_id="sess-1", memory=CancelMemory(), writer=w)
    outcome = await ops.compact(_prep(), reason="manual")
    assert outcome.stop_reason == "cancelled"
    envs = await r.read("sess-1", since=-1)
    assert isinstance(envs[1].payload, CompactionEnd)
    assert envs[1].payload.stop_reason == "cancelled"


@pytest.mark.asyncio
async def test_cj_b3_error_journals_error_end(journal) -> None:
    w, r = journal

    class BoomMemory(NullContextMemoryProvider):
        def before_compact(self, ctx, preparation, *, reason):  # type: ignore[no-untyped-def]
            raise RuntimeError("summarizer blew up")

    ops = MemoryOps(session_id="sess-1", memory=BoomMemory(), writer=w)
    with pytest.raises(RuntimeError, match="summarizer"):
        await ops.compact(_prep(), reason="overflow")
    envs = await r.read("sess-1", since=-1)
    assert isinstance(envs[0].payload, CompactionStart)
    assert isinstance(envs[1].payload, CompactionEnd)
    assert envs[1].payload.stop_reason == "error"


@pytest.mark.asyncio
async def test_cj_b4_proceed_default_pairs_without_summary(journal) -> None:
    w, r = journal
    ops = MemoryOps(
        session_id="sess-1",
        memory=NullContextMemoryProvider(),
        writer=w,
    )
    outcome = await ops.compact(_prep(), reason="threshold")
    assert outcome.stop_reason == "deferred"
    envs = await r.read("sess-1", since=-1)
    assert envs[0].payload.compaction_id == envs[1].payload.compaction_id
    assert envs[1].payload.stop_reason == "deferred"
    assert envs[1].payload.summary_digest is None


@pytest.mark.asyncio
async def test_cj_r1_reader_unpaired_compaction(journal) -> None:
    w, r = journal
    await w.append(
        "sess-1",
        CompactionStart(
            compaction_id="cmp-crash",
            reason="threshold",
            tokens_before=88_000,
        ),
        turn_id=None,
        trace_id=None,
    )
    unpaired = await r.unpaired_compaction_starts("sess-1")
    assert len(unpaired) == 1
    assert unpaired[0].compaction_id == "cmp-crash"


def test_cj_c1_contracts_mention_compaction_pair() -> None:
    root = Path(__file__).resolve().parents[4]
    body = (root / "contracts" / "memory.md").read_text()
    assert "compaction_start" in body
    assert "compaction_end" in body


def test_cj_a1_session_journal_api_no_memory_ops_provider_leak() -> None:
    src = Path(__file__).resolve().parents[2] / "src" / "supervisor"
    banned = ("BasicCompactionProvider", "FakeModelGatewayClient", "NullContextMemoryProvider")
    for pkg in ("journal", "api"):
        for path in (src / pkg).rglob("*.py"):
            text = path.read_text()
            for needle in banned:
                assert needle not in text, f"{path}: {needle}"
    session_root = src / "session"
    for path in session_root.rglob("*.py"):
        if path.name == "memory_ops.py":
            continue
        text = path.read_text()
        for needle in banned:
            assert needle not in text, f"{path}: {needle}"
