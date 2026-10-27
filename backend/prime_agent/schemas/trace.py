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
    summary: str | None = None
    """One sentence on what the run did and how it ended."""
    cost: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    steps: int = 0
    tool_calls: int = 0
    tool_errors: int = 0
    denials: int = 0
    errors: int = 0
    checks_failed: int = 0
    feedback_good: int = 0
    feedback_bad: int = 0
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


class TraceScore(BaseModel):
    """A verdict on a run or a span: an automatic check, or a person's feedback."""

    id: str
    span_id: str | None = None
    name: str
    topic: str | None = None
    source: str
    label: str
    value: float | None = None
    reason: str | None = None
    comment: str | None = None
    evaluator: str
    mine: bool = False
    """The user's own feedback (so the page shows their choice and can take it back)."""
    created_ms: int


class FeedbackRequest(BaseModel):
    good: bool
    span_id: str | None = Field(default=None, max_length=32)
    comment: str | None = Field(default=None, max_length=2000)


class VersionQuality(BaseModel):
    """How one version of an agent did across the user's finished runs."""

    version: int | None = None
    composition_digest: str | None = None
    runs: int
    answered: int
    failed: int
    runs_failing_checks: int
    feedback_good: int
    feedback_bad: int
    avg_duration_ms: int | None = None
    avg_cost: float = 0.0
    last_run_ms: int | None = None


class AgentQualityResponse(BaseModel):
    agent_id: str
    versions: list[VersionQuality]


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
    summary: str | None = None
    rollup: dict[str, Any]
    spans: list[dict[str, Any]]
    scores: list[TraceScore] = Field(default_factory=list)


class IssueExample(BaseModel):
    session_id: str
    """The product's session id."""
    run_id: str
    span_id: str | None = None
    started_ms: int | None = None
    input: str | None = None
    summary: str | None = None
    reason: str | None = None
    version: int | None = None


class IssueVersion(BaseModel):
    version: int | None = None
    composition_digest: str | None = None
    runs: int


class Issue(BaseModel):
    """One way runs go wrong: what judged it, what it is about, how often, where to look."""

    key: str
    title: str
    source: str
    name: str
    topic: str | None = None
    runs: int
    last_seen_ms: int
    agent_id: str | None = None
    agent_name: str | None = None
    versions: list[IssueVersion]
    examples: list[IssueExample]


class IssuesResponse(BaseModel):
    issues: list[Issue]


class Judge(BaseModel):
    id: str
    name: str
    rubric: str
    sample_rate: float
    model: str | None = None
    version: int
    enabled: bool
    updated_ms: int


class JudgeRequest(BaseModel):
    name: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9][A-Za-z0-9 _.-]*$")
    rubric: str = Field(min_length=10, max_length=4000)
    sample_rate: float = Field(default=1.0, ge=0.0, le=1.0)
    model: str | None = Field(default=None, max_length=255)
    enabled: bool = True


class JudgesResponse(BaseModel):
    agent_id: str
    judges: list[Judge]
