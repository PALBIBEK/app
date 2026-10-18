"""Conformance: every AgentEvent has a defined projection (Phase1 §3.2)."""

from __future__ import annotations

from typing import get_args

from supervisor.agent.events import AgentEvent
from supervisor.obs.projector import PROJECTION_TABLE


def test_every_agent_event_has_projection_row() -> None:
    declared = {get_args(AgentEvent)[i].__name__ for i in range(len(get_args(AgentEvent)))}
    # get_args returns the union members
    names = {t.__name__ for t in get_args(AgentEvent)}
    assert names == declared
    missing = names - set(PROJECTION_TABLE)
    assert not missing, f"missing projection rows: {missing}"


def test_sandbox_not_in_projection_table_as_agent_event() -> None:
    assert "Sandbox" not in PROJECTION_TABLE
