"""Run traces as the product shows them: the supervisor's runs, named by the user's own
session, agent and the agent's version number."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class RunSummary(BaseModel):
    """One run, as a list of runs shows it."""

    session_id: str
    """The product's session id (not the supervisor's)."""
    run_id: str
    trace_id: str
    status: str
    reason: str | None = None
    started_ms: int
    ended_ms: int | None = None
    duration_ms: int | None = None
    agent_id: str | None = None
    agent_name: str | None = None
    version: int | None = None
    """The agent version the session ran (None when not one of its releases)."""
    session_title: str | None = None
    input: str | None = None
    output: str | None = None
    cost: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    steps: int = 0
    tool_calls: int = 0
    tool_errors: int = 0
    denials: int = 0
    errors: int = 0
    time: dict[str, int] = Field(default_factory=dict)
    tools: dict[str, int] = Field(default_factory=dict)
    models: list[str] = Field(default_factory=list)


class TraceListResponse(BaseModel):
    items: list[RunSummary]
    next_before_ms: int | None = None
    """Pass back as ``before_ms`` for the next page; None at the end."""


class SessionTracesResponse(BaseModel):
    session_id: str
    title: str | None = None
    status: str
    agent_id: str | None = None
    agent_name: str | None = None
    version: int | None = None
    items: list[RunSummary]


class RunTraceResponse(BaseModel):
    """A run's spans (flat, each naming its parent) and rollup; span content is fetched per span."""

    session_id: str
    run_id: str
    trace_id: str
    agent_id: str | None = None
    agent_name: str | None = None
    version: int | None = None
    composition_digest: str | None = None
    session_title: str | None = None
    rollup: dict[str, Any]
    spans: list[dict[str, Any]]
