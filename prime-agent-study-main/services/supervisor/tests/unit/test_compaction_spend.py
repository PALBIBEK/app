"""A compaction's summary calls count whatever the provider decides (Pi Durable records
a compaction's usage before it looks at the result)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from supervisor.seams.interfaces.context_memory import CompactionDecision
from supervisor.temporal.context_memory import PinnedContextMemory

SPENT = {"input_tokens": 120, "output_tokens": 30, "cost": 0.004}


def _binding(decision: CompactionDecision) -> Any:
    memory = SimpleNamespace(
        before_compact=lambda context, preparation, reason: decision,
        estimate_tokens=lambda messages: 10,
    )
    return SimpleNamespace(memory=memory, context=None)


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["cancel", "proceed_default"])
async def test_a_compaction_keeps_what_its_summary_spent(action: str) -> None:
    end = await PinnedContextMemory._summarise(
        "cmp-1", _binding(CompactionDecision(action=action, spent=SPENT)), preparation=None, tail=[]  # type: ignore[arg-type]
    )
    assert end.usage is not None
    assert (end.usage.input_tokens, end.usage.output_tokens) == (120, 30)
    assert end.stop_reason == ("cancelled" if action == "cancel" else "ok")


@pytest.mark.asyncio
async def test_a_compaction_that_spent_nothing_reports_no_usage() -> None:
    end = await PinnedContextMemory._summarise(
        "cmp-1", _binding(CompactionDecision(action="cancel")), preparation=None, tail=[]  # type: ignore[arg-type]
    )
    assert end.usage is None
