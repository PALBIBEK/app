"""Datasets and experiments as the product shows them."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class DatasetCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=2000)


class AddRunRequest(BaseModel):
    session_id: str = Field(min_length=1, max_length=36)
    run_id: str = Field(min_length=1, max_length=64)
    expected: str | None = Field(default=None, max_length=4000)
    """What a good answer does, in your words (optional)."""


class DatasetItemOut(BaseModel):
    id: str
    input: str
    expected: str | None = None
    source_session_id: str | None = None
    source_run_id: str | None = None
    source_version: int | None = None
    source_status: str | None = None
    created_at: datetime


class ExperimentStats(BaseModel):
    """How a version did on the dataset's items."""

    items: int
    finished: int
    answered: int
    failed: int
    needs_approval: int
    checks_failed: int
    """Runs that failed at least one automatic check."""
    judge_pass: int
    judge_fail: int
    avg_cost: float | None = None
    avg_duration_ms: int | None = None


class ExperimentOut(BaseModel):
    id: str
    dataset_id: str
    label: str
    release_version: int | None = None
    composition_digest: str | None = None
    status: str
    error: str | None = None
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    stats: ExperimentStats | None = None


class DatasetOut(BaseModel):
    id: str
    agent_id: str
    name: str
    description: str | None = None
    items: int
    experiments: int
    created_at: datetime


class DatasetDetail(BaseModel):
    id: str
    agent_id: str
    agent_name: str | None = None
    name: str
    description: str | None = None
    items: list[DatasetItemOut]
    experiments: list[ExperimentOut]


class ExperimentStart(BaseModel):
    version: int | None = Field(default=None, ge=1)
    """The release to run; none: the agent's live version."""


class Verdict(BaseModel):
    name: str
    source: str
    label: str
    reason: str | None = None


class ExperimentItemResult(BaseModel):
    item_id: str
    position: int
    input: str
    expected: str | None = None
    status: str
    """How the replay went: ``done``, ``needs_approval``, ``timed_out``, ``failed``, ``pending``."""
    error: str | None = None
    session_id: str | None = None
    run_id: str | None = None
    run_status: str | None = None
    """How the run ended (``ok``, ``error`` …), once it has."""
    summary: str | None = None
    cost: float | None = None
    duration_ms: int | None = None
    verdicts: list[Verdict] = Field(default_factory=list)


class ExperimentDetail(BaseModel):
    experiment: ExperimentOut
    dataset_name: str
    results: list[ExperimentItemResult]


class ComparisonRow(BaseModel):
    item_id: str
    input: str
    a: ExperimentItemResult | None = None
    b: ExperimentItemResult | None = None
    change: str
    """``better``, ``worse``, ``same`` or ``unknown`` (b against a)."""


class ExperimentComparison(BaseModel):
    a: ExperimentOut
    b: ExperimentOut
    rows: list[ComparisonRow]
    better: int
    worse: int


# --- proposals: changes learned from traces, gated on a dataset -------------------------------


class ProposalCreate(BaseModel):
    dataset_id: str = Field(min_length=1, max_length=36)
    """The saved runs the candidate must not do worse on."""
    notes: str | None = Field(default=None, max_length=2000)
    """What you want addressed, in your words (optional)."""


class PromptSectionOut(BaseModel):
    id: str
    text: str


class PromptOut(BaseModel):
    persona: str
    sections: list[PromptSectionOut]


class EvidenceExample(BaseModel):
    session_id: str | None = None
    """The product session the failing run was in, when it is the user's."""
    run_id: str | None = None
    input: str | None = None
    summary: str | None = None
    reason: str | None = None


class Evidence(BaseModel):
    title: str
    runs: int
    examples: list[EvidenceExample] = Field(default_factory=list)


class GateItem(BaseModel):
    item_id: str
    input: str
    change: str
    """``better``, ``worse``, ``same`` or ``unknown`` (did not finish)."""


class Gate(BaseModel):
    passed: bool
    reasons: list[str]
    """Why it passed or not, in sentences."""
    better: int
    worse: int
    items: list[GateItem] = Field(default_factory=list)


class ProposalOut(BaseModel):
    id: str
    agent_id: str
    agent_name: str | None = None
    dataset_id: str
    dataset_name: str | None = None
    status: str
    error: str | None = None
    notes: str | None = None
    base_version: int
    rationale: str | None = None
    changed: list[str] = Field(default_factory=list)
    gate: Gate | None = None
    release_version: int | None = None
    created_at: datetime
    decided_at: datetime | None = None


class ProposalDetail(ProposalOut):
    base_prompt: PromptOut
    candidate_prompt: PromptOut | None = None
    evidence: list[Evidence] = Field(default_factory=list)
    baseline: ExperimentOut | None = None
    candidate: ExperimentOut | None = None
