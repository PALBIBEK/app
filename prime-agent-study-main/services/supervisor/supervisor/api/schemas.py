"""API request/response schemas — wire shapes, not internal types."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


class SessionMemoryOptions(BaseModel):
    """Which notebooks (by name) a session writes and reads, and QM's memory policy."""

    write: str | None = "default"
    read: list[str] = Field(default_factory=lambda: ["default"])
    recall: Literal["off", "writable", "visible"] = "visible"
    capture: Literal["off", "writable"] = "writable"
    strategy: Literal["per-turn", "agent-only"] = "per-turn"
    capture_quiet_s: float = Field(default=180.0, ge=0, le=86_400)
    capture_max_turns: int = Field(default=10, ge=1, le=100)
    open: bool = False


class CreateSessionRequest(BaseModel):
    harness_version: str
    runtime: str | None = None  # default: the harness manifest's runtime
    composition_id: str | None = None
    composition_digest: str | None = None
    scope_id: str | None = None
    memory: SessionMemoryOptions | None = None


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
    is_busy: bool | None = None


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


COMMAND_ID_MAX: int = 200
"""Long enough for any UUID or ULID scheme; a command id becomes part of a Temporal
update id, which has a length limit (a 10k id was a 500)."""


def _no_nul(value: str) -> str:
    """NUL is not text: Postgres refuses it in text, and in JSON it breaks every query
    that reads the field (one such message broke SQL reads of every stored event)."""
    if "\x00" in value:
        raise ValueError("must not contain NUL characters")
    return value


class PostMessageRequest(BaseModel):
    text: str = Field(min_length=1)
    command_id: str = Field(min_length=1, max_length=COMMAND_ID_MAX)
    steer: bool = False
    """Sent while a run works: join that run at its next step (pi's steering) instead of
    waiting for it to end. Ignored when no run is working."""

    _text_no_nul = field_validator("text", "command_id")(_no_nul)


class PostMessageResponse(BaseModel):
    turn_id: str
    status: str = "accepted"
    queued: bool | None = None
    """True when the message waits behind a running turn; None when not known."""
    steered: bool = False
    """The message joined the running turn (``turn_id``) rather than starting its own."""
    held: bool = False
    """No worker is up: the message is kept and runs once one comes."""


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


class SubagentOut(BaseModel):
    """A subagent below a session (``supervisor.session.subagent_view``)."""

    id: str
    parent_id: str
    depth: int
    label: str | None = None
    task: str | None = None
    kind: Literal["foreground", "background", "continuable"]
    mode: Literal["spawn", "fork"]
    status: Literal["starting", "running", "ready", "completed", "failed", "cancelled"]
    """running while a run is open; ready: a continuable subagent waiting for its next
    message; starting: before its first run; else how its run ended (``outcome``)."""
    outcome: Literal["completed", "failed", "cancelled"] | None = None
    answer: str | None = None
    answer_truncated: bool = False
    open: bool
    call_id: str | None = None
    cost_usd: float
    created_at: datetime


class SubagentListOut(BaseModel):
    subagents: list[SubagentOut]


class ArtifactMetadataOut(BaseModel):
    """Who published a version and when, and who deprecated it."""

    created_by: str
    created_at: str
    updated_at: str
    owner_id: str | None = None
    deprecated_by: str | None = None
    deprecated_at: str | None = None


class CatalogArtifactOut(BaseModel):
    kind: str
    artifact_id: str
    digest: str
    status: str
    semver: str
    protocol_version: int = 1
    metadata: ArtifactMetadataOut | None = None
    # The artifact itself, on a single read: what a user copies and changes to make a new version.
    body: dict[str, Any] | None = None


# --- long-term memory (notebooks) ----------------------------------------------


class NotebookCreate(BaseModel):
    agent_pin: str = Field(min_length=1)
    name: str = Field(min_length=1, max_length=64)
    label: str | None = Field(default=None, max_length=200)


class NotebookPatch(BaseModel):
    archived: bool


class NotebookOut(BaseModel):
    id: str
    agent_pin: str
    name: str
    label: str | None
    revision: int
    created_at: int
    archived: bool
    facts: int
    bytes: int
    updated_at: int | None


class FactOut(BaseModel):
    id: str
    text: str
    sensitivity: str
    sources: list[str]
    source_unknown: bool
    author: str | None
    created_at: int
    updated_at: int


class FactsAdd(BaseModel):
    facts: list[str] = Field(min_length=1, max_length=100)


class FactEdit(BaseModel):
    id: str
    text: str = Field(min_length=1, max_length=4000)


class FactsEdit(BaseModel):
    edits: list[FactEdit] = Field(min_length=1, max_length=100)


class FactsDelete(BaseModel):
    ids: list[str] = Field(min_length=1, max_length=500)


class FactsAdded(BaseModel):
    added: int


class NotebookRestore(BaseModel):
    revision: int = Field(ge=0)


class RevisionOut(BaseModel):
    revision: int
    fact_id: str
    op: str
    text: str
    sensitivity: str
    at: int
    author: str | None


class MemorySearch(BaseModel):
    agent_pin: str = Field(min_length=1)
    query: str = Field(min_length=1, max_length=2000)
    notebook_id: str | None = None
    limit: int = Field(default=20, ge=1, le=50)


class MemoryHit(BaseModel):
    notebook_id: str
    fact: str


class FactsRemoved(BaseModel):
    removed: int
