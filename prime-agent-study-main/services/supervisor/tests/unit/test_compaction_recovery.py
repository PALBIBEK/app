"""Recovery treats unpaired compaction_start like unpaired tool_start."""

from __future__ import annotations

import pytest

from supervisor.agent.events import CompactionStart
from supervisor.session.recovery import Recovery, ResumeState


@pytest.mark.asyncio
async def test_unpaired_compaction_start_needs_review(session_env) -> None:
    m = session_env["manager"]
    writer = session_env["writer"]
    registry = session_env["registry"]
    reader = session_env["reader"]
    catalog = session_env["catalog"]
    sid = await m.create(tenant_id="t1", harness_version="kg-v1", runtime_name="echo")
    rt = registry.remove(sid)
    if rt is not None:
        await rt.stop()
    await writer.append(
        sid,
        CompactionStart(
            compaction_id="cmp-orphan",
            reason="threshold",
            tokens_before=99_000,
        ),
        turn_id="t",
        trace_id=None,
    )
    row = await m._get_session_row(sid)
    recovery = Recovery(registry=registry, reader=reader, catalog=catalog)
    decision = await recovery.decide(sid, session_row=row)
    assert decision.state == ResumeState.NEEDS_REVIEW
    assert decision.unpaired_compaction_ids == ("cmp-orphan",)
    await m.terminate(sid)
