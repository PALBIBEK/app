"""D7 §5 B rule 7 / T6–T7 — recall fail-soft + journal memory_recall."""

from __future__ import annotations

import pytest

from supervisor.agent.events import MemoryRecall
from supervisor.seams.context_memory import RecallBlock
from supervisor.seams.providers.null_context_memory import NullContextMemoryProvider
from supervisor.session.memory_ops import MemoryOps, RecallMemoryOutcome


class _RecallStub:
    def __init__(self, block: RecallBlock | None = None, *, boom: bool = False) -> None:
        self._block = block
        self._boom = boom

    def estimate_tokens(self, messages):  # type: ignore[no-untyped-def]
        return NullContextMemoryProvider().estimate_tokens(messages)

    def before_compact(self, ctx, preparation, *, reason):  # type: ignore[no-untyped-def]
        return NullContextMemoryProvider().before_compact(ctx, preparation, reason=reason)

    def recall_for_turn(self, ctx, *, user_prompt: str):  # type: ignore[no-untyped-def]
        if self._boom:
            raise RuntimeError("d7 recall backend down")
        _ = ctx, user_prompt
        return self._block

    def append_plug_state(self, ctx, custom_type, data):  # type: ignore[no-untyped-def]
        return NullContextMemoryProvider().append_plug_state(ctx, custom_type, data)


@pytest.mark.asyncio
async def test_t6_recall_miss_journals_not_injected_run_continues(journal) -> None:
    """T6: empty recall → memory_recall injected=false; ops does not raise."""
    w, r = journal
    ops = MemoryOps(session_id="d7-sess", memory=_RecallStub(None), writer=w)
    outcome = await ops.recall("anything")
    assert isinstance(outcome, RecallMemoryOutcome)
    assert outcome.injected is False
    ev = (await r.read("d7-sess", since=-1))[0].payload
    assert isinstance(ev, MemoryRecall)
    assert ev.injected is False


@pytest.mark.asyncio
async def test_recall_fail_soft_never_raises(journal) -> None:
    """Architecture rule 7: provider exception → soft miss, journaled, no raise."""
    w, r = journal
    ops = MemoryOps(session_id="d7-sess", memory=_RecallStub(boom=True), writer=w)
    outcome = await ops.recall("query")
    assert outcome.injected is False
    ev = (await r.read("d7-sess", since=-1))[0].payload
    assert isinstance(ev, MemoryRecall)
    assert ev.injected is False


@pytest.mark.asyncio
async def test_t7_recall_hit_journals_injected(journal) -> None:
    """T7 (ContextMemory half): successful recall journals injected=true + text."""
    w, r = journal
    block = RecallBlock(text="User prefers dark mode", source="semantic", score=0.9)
    ops = MemoryOps(session_id="d7-sess", memory=_RecallStub(block), writer=w)
    outcome = await ops.recall("preferences?")
    assert outcome.injected is True
    assert outcome.text and "dark mode" in outcome.text
    ev = (await r.read("d7-sess", since=-1))[0].payload
    assert isinstance(ev, MemoryRecall)
    assert ev.injected is True
    assert ev.token_estimate > 0
