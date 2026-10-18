"""Integration — MemoryOps via SessionManager journals compaction pair."""

from __future__ import annotations

import pytest

from supervisor.agent.events import CompactionEnd, CompactionStart
from supervisor.seams.compaction import SUMMARY_MARKER
from supervisor.seams.context_memory import CompactionPreparation
from supervisor.seams.factory import build_runtime_seams
from supervisor.seams.providers.basic_compaction import BasicCompactionProvider
from supervisor.seams.providers.fake_gateway import FakeModelGatewayClient


@pytest.mark.asyncio
async def test_compact_memory_journals_pair(session_env) -> None:
    m = session_env["manager"]
    reader = session_env["reader"]
    gateway = FakeModelGatewayClient(assistant_text="## Goal\nintegration ok")
    m._fallback_seams = build_runtime_seams(
        context_memory=BasicCompactionProvider(gateway),
    )
    sid = await m.create(tenant_id="t1", harness_version="kg-v1", runtime_name="echo")
    prep = CompactionPreparation(
        first_kept_entry_id="e0",
        messages_to_summarize=[{"role": "user", "content": "history"}],
        tokens_before=100_000,
        previous_summary=None,
        context_window=128_000,
        reserve_tokens=16_384,
    )
    result = await m.compact_memory(
        sid,
        tenant_id="t1",
        preparation=prep,
        reason="threshold",
    )
    assert result.stop_reason == "ok"
    assert result.summary is not None
    assert SUMMARY_MARKER in result.summary
    envs = await reader.read(sid, since=-1)
    compaction_types = [
        type(e.payload).__name__
        for e in envs
        if isinstance(e.payload, (CompactionStart, CompactionEnd))
    ]
    assert compaction_types == ["CompactionStart", "CompactionEnd"]
    assert await reader.unpaired_compaction_starts(sid) == []
    await m.terminate(sid)
