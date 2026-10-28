"""Session BFF schemas (D4) — product wire, no AI digests on create."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

SessionStatus = Literal[
    "active",
    "paused",
    "terminated",
    "failed",
    "awaiting_approval",
    "budget_exceeded",
]


class CreateSessionFromHarness(BaseModel):
    """Browser may supply harness_id only — pins come from ensure_runnable_pin."""

    model_config = ConfigDict(extra="forbid")

    harness_id: str = Field(..., min_length=1)
    scope_id: str | None = Field(default=None, min_length=1)


class SessionPinSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    harness_version: str
    runtime: str
    composition_id: str | None = None
    composition_digest: str | None = None


class SessionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    harness_id: str
    status: str
    harness_version: str
    runtime: str
    composition_id: str | None = None
    composition_digest: str | None = None
    scope_id: str | None = None
    title: str | None = None
    message_count: int = 0
    total_cost: float = 0.0
    last_seq: int = 0
    is_busy: bool | None = None
    created_at: datetime
    updated_at: datetime
    pin: SessionPinSnapshot


class SessionStartupResponse(BaseModel):
    """How a new session's start stands: ``starting`` (``worker_available`` false means no
    agent worker is running to pick it up), ``ready``, ``failed`` (``reason`` says why), or
    ``closed``."""

    status: str
    worker_available: bool
    reason: str | None = None
    kind: str | None = None


RUN_TERMINAL = frozenset({"completed", "failed", "cancelled", "incomplete"})


class RunResponse(BaseModel):
    """One run (what happened to one message): queued, in_progress, requires_action, then
    completed, failed, cancelled or incomplete; with its final answer, usage and error."""

    run_id: str
    command_id: str
    status: str
    created_at_ms: int
    started_at_ms: int | None = None
    ended_at_ms: int | None = None
    stop_reason: str | None = None
    output_text: str | None = None
    error: dict[str, str] | None = None
    usage: dict[str, Any] = Field(default_factory=dict)
    steps: int = 0
    tool_calls: int = 0
    pending_approval_id: str | None = None


class SessionListResponse(BaseModel):
    data: list[SessionResponse]
    total: int
    page: int
    page_size: int


class SessionMessageRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: str = Field(..., min_length=1)
    command_id: str | None = Field(default=None, min_length=1)
    # Sent while a run works: join it at its next step (steering) instead of queueing.
    steer: bool = False


class SessionMessageResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    turn_id: str
    command_id: str
    status: str = "accepted"
    queued: bool = False  # true when it waits behind a turn already running
    steered: bool = False  # true when it joined the running turn (turn_id) instead
    held: bool = False  # true when no worker was up: kept, it runs once one comes


class SessionToolInvokeRequest(BaseModel):
    """BFF proxy body for supervisor tools/invoke (D6)."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1)
    arguments: dict = Field(default_factory=dict)
    tool_call_id: str | None = None


class SessionToolInvokeResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tool_call_id: str
    ok: bool
    payload: object | None = None
    error: str | None = None
    code: str | None = None


class SessionApprovalOut(BaseModel):
    """Pending / resolved approval as returned by supervisor via BFF (D6 E)."""

    model_config = ConfigDict(extra="forbid")

    approval_id: str
    session_id: str
    prompt: str
    options: list[str]
    status: str
    choice: str | None = None
    timeout_s: float


class SessionApprovalChoice(BaseModel):
    model_config = ConfigDict(extra="forbid")

    choice: str = Field(..., min_length=1)


class ForkSessionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    at_seq: int | None = None


class ForkSessionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    parent_id: str


class SpawnSubagentRequest(BaseModel):
    """BFF body for supervisor subagents/spawn (D12 B).

    Product default ``lab_echo`` is the in-process echo stub (supervisor name
    may be ``lab_echo`` or legacy ``memory``) — never conflate with durable / LTM (D7).
    """

    model_config = ConfigDict(extra="forbid")

    prompt: str = Field(..., min_length=1)
    description: str = ""
    provider: str = Field(default="lab_echo", min_length=1)
    mode: str = Field(default="spawn", min_length=1)
    parent_depth: int = 0
    run_id: str | None = None
    tool_filter: list[str] | None = None
    persona: str | None = None
    depth_limit: int | None = None
    output_schema: dict[str, Any] | None = None


class SpawnSubagentResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    child_session_id: str
    text: str | None = None
    status: str | None = None
    run_id: str | None = None
    provider: str | None = None


class SessionChildOut(BaseModel):
    """Child row from supervisor GET …/children via BFF (D12 B)."""

    model_config = ConfigDict(extra="forbid")

    id: str
    mode: str
    provider: str
    parent_id: str
    run_id: str
    label: str | None = None
    activity: str = "running"
    depth: int | None = None
    session_id: str | None = None


class PinnedPart(BaseModel):
    """One part of the composition a session runs, at the version it pinned."""

    kind: str
    role: str
    artifact_id: str
    digest: str
    semver: str | None = None
    status: str
    settings: dict[str, Any] = Field(default_factory=dict)


class SessionPinsResponse(BaseModel):
    """What a session runs: its agent's version and every pinned part."""

    composition_id: str
    composition_digest: str
    composition_semver: str | None = None
    composition_status: str
    release_version: int | None = None
    """The agent's version number this composition was published as (None if not one of its releases)."""
    harness_version: str
    model_route: str | None = None
    mcp_configured: bool = False
    acp_configured: bool = False
    parts: list[PinnedPart]
