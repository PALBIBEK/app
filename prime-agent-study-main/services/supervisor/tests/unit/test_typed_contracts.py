"""Bugs the type checker found, pinned by behaviour.

Each test fails on the code as it was before the fix: the checker saw a call that
could not work, and these show what a user would have hit.
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from supervisor.agent.base import AgentPhase
from supervisor.agent.runtimes.harness.state import from_get_state
from supervisor.catalog.core.errors import DigestMissing
from supervisor.catalog.loading.loader import load_composition_catalog
from supervisor.config import TemporalSettings
from supervisor.harness import BudgetSpec
from supervisor.policy.budget.tracker import BudgetTracker


def test_temporal_settings_default_to_a_local_server(monkeypatch: pytest.MonkeyPatch) -> None:
    """With slots, the defaults read as ``cls.address`` were descriptors, not strings."""
    for name in ("TEMPORAL_ADDRESS", "TEMPORAL_NAMESPACE", "TEMPORAL_TASK_QUEUE"):
        monkeypatch.delenv(name, raising=False)
    settings = TemporalSettings.from_env()
    assert (settings.address, settings.namespace, settings.task_queue) == (
        "localhost:7233",
        "default",
        "supervisor-sessions",
    )


@pytest.mark.parametrize(
    ("flags", "phase"),
    [
        ({}, AgentPhase.READY),
        ({"isStreaming": True}, AgentPhase.EXECUTING),
        ({"isStreaming": True, "isCompacting": True}, AgentPhase.COMPACTING),
    ],
)
def test_harness_state_maps_its_flags_to_a_phase(flags: dict[str, bool], phase: AgentPhase) -> None:
    """``get_state()`` passed ``is_busy=`` to AgentState, which has no such field."""
    state = from_get_state({"sessionId": "s1", "pendingMessageCount": 0, **flags})
    assert state.phase is phase
    assert state.is_busy is (phase is AgentPhase.EXECUTING)
    assert state.is_compacting is (phase is AgentPhase.COMPACTING)
    assert state.settled is (phase is AgentPhase.READY)


def test_a_composition_naming_an_unknown_artifact_is_digest_missing(tmp_path: Path) -> None:
    """DigestMissing was called positionally and raised a TypeError instead."""
    (tmp_path / "compositions").mkdir()
    (tmp_path / "compositions" / "c.json").write_text(
        json.dumps(
            {
                "name": "c",
                "semver": "1.0.0",
                "harness_version": "githi-v1",
                "prompt_pack": {"id": "no-such-prompt"},
                "tool_set": {"id": "no-such-tools"},
            }
        )
    )
    with pytest.raises(DigestMissing) as caught:
        load_composition_catalog(tmp_path)
    assert caught.value.detail["artifact_id"] == "no-such-prompt"


def test_a_manifest_budget_limits_cost_exactly() -> None:
    """The manifest states USD as a float; the tracker compares money as Decimal."""
    tracker = BudgetTracker(session_factory=None)  # type: ignore[arg-type]
    tracker.configure("s", BudgetSpec(max_cost_per_session=0.3))
    at_limit = tracker._evaluate("s", cost=Decimal("0.1") * 3, tokens=0)
    assert at_limit.max_cost_usd == Decimal("0.3") and not at_limit.exceeded
    over = tracker._evaluate("s", cost=Decimal("0.31"), tokens=0)
    assert over.exceeded and over.exceeded_reason is not None
