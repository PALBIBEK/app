"""The Pi harness's get_state maps onto AgentState (it used to raise TypeError on every read)."""

from __future__ import annotations

import pytest

from supervisor.agent.base import AgentPhase
from supervisor.agent.runtimes.harness.state import from_get_state


@pytest.mark.parametrize(
    ("flags", "phase", "busy", "settled"),
    [
        ({}, AgentPhase.READY, False, True),
        ({"isStreaming": True}, AgentPhase.EXECUTING, True, False),
        ({"isCompacting": True, "isStreaming": True}, AgentPhase.COMPACTING, False, False),
    ],
)
def test_get_state_maps_to_a_phase(
    flags: dict[str, bool], phase: AgentPhase, busy: bool, settled: bool
) -> None:
    state = from_get_state(
        {"sessionId": "s1", "messageCount": 3, "model": {"id": "m", "contextWindow": 1000}, **flags}
    )
    assert (state.phase, state.is_busy, state.settled) == (phase, busy, settled)
    assert (state.message_count, state.model, state.context_window) == (3, "m", 1000)
