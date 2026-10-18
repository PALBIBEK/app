"""D7 §5 B / T4–T5 — ContextMemory compaction journals paired start/end."""

from __future__ import annotations

import pytest

from supervisor.agent.events import CompactionEnd, CompactionStart
from supervisor.seams.providers.basic_compaction import BasicCompactionProvider
from supervisor.seams.providers.fake_gateway import FakeModelGatewayClient
from supervisor.session.compaction_state import unpaired_compaction_starts
from supervisor.session.memory_ops import MemoryOps
from supervisor.session.recovery import Recovery, ResumeState
from tests.d7_acceptance.conftest import compaction_prep


@pytest.mark.asyncio
async def test_t4_compact_journals_paired_compaction_start_end(journal) -> None:
    """T4 / §5 B: compaction with profile-on provider → paired journal events."""
    w, r = journal
    gateway = FakeModelGatewayClient(assistant_text="## Summary\nd7 compacted")
    ops = MemoryOps(
        session_id="d7-sess",
        memory=BasicCompactionProvider(gateway, summarizer_model="cheap-summary"),
        writer=w,
        tenant_id="d7-tenant",
    )
    outcome = await ops.compact(compaction_prep(), reason="threshold")
    assert outcome.stop_reason == "ok"
    assert outcome.compaction_id.startswith("cmp-")

    envs = await r.read("d7-sess", since=-1)
    types = [type(e.payload).__name__ for e in envs]
    assert "CompactionStart" in types
    assert "CompactionEnd" in types
    starts = [e.payload for e in envs if isinstance(e.payload, CompactionStart)]
    ends = [e.payload for e in envs if isinstance(e.payload, CompactionEnd)]
    assert len(starts) == 1 and len(ends) == 1
    assert starts[0].compaction_id == ends[0].compaction_id == outcome.compaction_id
    assert ends[0].stop_reason == "ok"
    assert unpaired_compaction_starts(envs) == []


@pytest.mark.asyncio
async def test_t5_unpaired_compaction_start_needs_review(session_env) -> None:
    """T5 / §5 B: unpaired compaction_start → recovery NEEDS_REVIEW (like tool_start)."""
    from supervisor.agent.events import CompactionStart as CS
    from supervisor.session.recovery import Recovery as Rec

    m = session_env["manager"]
    writer = session_env["writer"]
    registry = session_env["registry"]
    reader = session_env["reader"]
    catalog = session_env["catalog"]
    sid = await m.create(tenant_id="d7-t", harness_version="kg-v1", runtime_name="echo")
    rt = registry.remove(sid)
    if rt is not None:
        await rt.stop()
    await writer.append(
        sid,
        CS(compaction_id="cmp-d7-orphan", reason="threshold", tokens_before=99_000),
        turn_id="t",
        trace_id=None,
    )
    row = await m._get_session_row(sid)
    decision = await Rec(registry=registry, reader=reader, catalog=catalog).decide(
        sid, session_row=row
    )
    assert decision.state == ResumeState.NEEDS_REVIEW
    assert "cmp-d7-orphan" in decision.unpaired_compaction_ids
    await m.terminate(sid)
