"""API request/response schemas — wire shapes, not internal types."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class CreateSessionRequest(BaseModel):
    harness_version: str
    runtime: str | None = None  # default: the harness manifest's runtime
    composition_id: str | None = None
    composition_digest: str | None = None
    scope_id: str | None = None


class CreateSessionResponse(BaseModel):
    session_id: str
    harness_version: str
    runtime: str
    status: str = "active"


class SessionSummary(BaseModel):
    session_id: str
    status: str
    harness_version: str
    runtime: str
    last_seq: int
    title: str | None = None
    message_count: int = 0
    total_cost: float = 0.0


class SessionDetailResponse(BaseModel):
    session_id: str
    status: str
    harness_version: str
    runtime: str
    last_seq: int
    title: str | None = None
    message_count: int = 0
    is_busy: bool | None = None
    total_cost: float = 0.0


class PostMessageRequest(BaseModel):
    text: str = Field(min_length=1)
    command_id: str = Field(min_length=1)


class PostMessageResponse(BaseModel):
    turn_id: str
    status: str = "accepted"


class ForkSessionRequest(BaseModel):
    at_seq: int | None = None


class ForkSessionResponse(BaseModel):
    session_id: str


class WireEvent(BaseModel):
    """Versioned SSE / JSON event. Envelope flat; payload in data."""

    seq: int
    event_id: str
    turn_id: str | None = None
    type: str
    ts_ms: int
    data: dict[str, Any] = Field(default_factory=dict)


class ApprovalDecision(BaseModel):
    approved: bool
    note: str | None = None


class InvokeToolRequest(BaseModel):
    name: str = Field(min_length=1)
    arguments: dict[str, Any] = Field(default_factory=dict)
    tool_call_id: str | None = None


class InvokeToolResponse(BaseModel):
    tool_call_id: str
    ok: bool
    payload: Any = None
    error: str | None = None
    code: str | None = None


class CompactMemoryRequest(BaseModel):
    reason: str = "manual"  # manual | threshold | overflow
    first_kept_entry_id: str = Field(min_length=1)
    messages_to_summarize: list[dict[str, Any]] = Field(default_factory=list)
    tokens_before: int = Field(ge=0)
    previous_summary: str | None = None
    context_window: int = Field(default=128_000, ge=1)
    reserve_tokens: int = Field(default=16_384, ge=0)


class CompactMemoryResponse(BaseModel):
    compaction_id: str
    stop_reason: str
    summary: str | None = None
    tokens_before: int | None = None
    tokens_after: int | None = None
    summary_digest: str | None = None


class RecallMemoryRequest(BaseModel):
    user_prompt: str = Field(min_length=1)


class RecallMemoryResponse(BaseModel):
    block_id: str
    injected: bool
    source: str
    text: str | None = None
    token_estimate: int = 0


class SubagentCommandRequest(BaseModel):
    prompt: str = Field(min_length=1)
    description: str = ""
    provider: str = "memory"
    parent_depth: int = 0
    mode: str = "spawn"
    run_id: str | None = None
    tool_filter: list[str] | None = None
    persona: str | None = None
    depth_limit: int | None = None
    output_schema: dict[str, Any] | None = None


class SpawnChildResponse(BaseModel):
    child_session_id: str
    text: str | None = None
    status: str | None = None
    run_id: str | None = None


class ContinuableChildResponse(BaseModel):
    child_id: str
    message_id: str | None = None


class BackgroundJobResponse(BaseModel):
    job_id: str


class ChildMessageRequest(BaseModel):
    text: str = Field(min_length=1)


class ChildRowOut(BaseModel):
    id: str
    mode: str
    provider: str
    parent_id: str
    run_id: str
    label: str | None = None
    activity: str = "running"
    depth: int | None = None


class CatalogArtifactOut(BaseModel):
    kind: str
    artifact_id: str
    digest: str
    status: str
    semver: str
