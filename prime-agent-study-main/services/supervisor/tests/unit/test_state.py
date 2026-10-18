"""Parse get_state → AgentState (settled is computed, never stored)."""

from __future__ import annotations

import copy
import json
from pathlib import Path

from supervisor.agent.runtimes.harness.state import from_get_state

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "harness-get-state.json"


def test_settled_true_for_captured_idle_state() -> None:
    raw = json.loads(FIXTURE.read_text())
    state = from_get_state(raw)
    assert state.settled is True
    assert state.session_id == "019ff5c6-8cd1-7168-baad-6b90e9d0373d"
    assert state.is_busy is False
    assert state.is_compacting is False
    assert state.pending_count == 0
    assert state.message_count == 4
    assert state.model == "anthropic/claude-sonnet-4"
    assert state.context_window == 1_000_000


def test_is_streaming_true_makes_settled_false() -> None:
    raw = copy.deepcopy(json.loads(FIXTURE.read_text()))
    raw["isStreaming"] = True
    state = from_get_state(raw)
    assert state.is_busy is True
    assert state.settled is False


def test_missing_optional_fields_do_not_raise() -> None:
    state = from_get_state(
        {
            "sessionId": "s",
            "isStreaming": False,
            "isCompacting": False,
            "pendingMessageCount": 0,
            "messageCount": 0,
        }
    )
    assert state.session_id == "s"
    assert state.model is None
    assert state.context_window is None
    assert state.settled is True
