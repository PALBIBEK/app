"""Types shared by the API, the session workflow and its activities.

Imported inside the workflow sandbox, so this module must stay free of I/O and
of anything non-deterministic. Every type is small: conversation content lives
in the journal, never in workflow history (Temporal payloads are capped at 2 MB).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

WORKFLOW_ID_PREFIX = "session/"


def workflow_id(session_id: str) -> str:
    return f"{WORKFLOW_ID_PREFIX}{session_id}"


def job_workflow_id(session_id: str, job_id: str) -> str:
    """A background subagent job's workflow, named under its session's."""
    return f"{workflow_id(session_id)}/job/{job_id}"


def job_ids(op_id: str) -> tuple[str, str]:
    """(job_id, run_id) for the START_BACKGROUND op ``op_id``: same op, same job."""
    digest = uuid.uuid5(uuid.NAMESPACE_URL, f"yal-job:{op_id}").hex
    return f"job-{digest[:16]}", f"run-{digest[16:28]}"


# Signal a job's workflow sends its session's when it ends.
JOB_FINISHED_SIGNAL = "job_finished"

# Sent by a session's own tool step to run a background subagent it just journalled.
START_JOB_SIGNAL = "start_job"


class Activity(StrEnum):
    """Activity names. The workflow calls activities by name so it never imports their code."""

    CREATE = "session.create"
    # A new session's workflow whose session is a copy of another's journal.
    FORK = "session.fork"
    ADOPT = "session.adopt"
    PREPARE_TURN = "session.prepare_turn"
    RUN_TURN = "session.run_turn"
    RESPOND_APPROVAL = "session.respond_approval"
    TERMINATE = "session.terminate"
    # Workflow-driven (stepped) turns: one model call or one tool call per activity.
    BEGIN_RUN = "session.begin_run"
    MODEL_STEP = "session.model_step"
    TOOL_CALL = "session.tool_call"
    REQUEST_APPROVAL = "session.request_approval"
    RESOLVE_APPROVAL = "session.resolve_approval"
    END_RUN = "session.end_run"
    # Journal-only, so it runs on any worker: closes a call whose worker died.
    CLOSE_LOST_CALL = "session.close_lost_call"
    # Any other session operation (tool invoke, memory, subagents, workspace).
    SESSION_OP = "session.op"
    # A background subagent job's work and its end, on any worker.
    RUN_JOB = "subagent.run_job"
    END_JOB = "subagent.end_job"
    # Extract and save durable facts from a burst of a session's turns (any worker).
    CAPTURE_MEMORY = "session.capture_memory"


class Phase(StrEnum):
    CREATING = "creating"
    IDLE = "idle"
    RUNNING = "running"
    TERMINATED = "terminated"
    FAILED = "failed"


# ApplicationError types that retrying cannot fix. They carry the domain exception's
# class name so the API maps them to the same HTTP status as before.
NON_RETRYABLE = (
    "NotOffered",
    "DigestMissing",
    "HarnessVersionMismatch",
    "HarnessNotOffered",
    "IncompatibleHarness",
    "CatalogError",
    "QuotaExceeded",
    "GrantDenied",
    "NotScopeMember",
    "SessionNotFound",
    "SessionTerminated",
    "SessionAwaitingApproval",
    "BudgetExceeded",
    "RecoveryBlocked",
    "TurnInterrupted",
    "ApprovalError",
    "ApprovalNotFound",
    "ApprovalAlreadyResponded",
    "ApprovalTimedOut",
    "InvalidApprovalChoice",
    # Session operations: missing seams, bad requests, workspace preconditions.
    "ToolInvokeNotConfigured",
    "SubagentNotConfigured",
    "MemoryNotConfigured",
    "WorkspaceNotReadyError",
    "WorkspaceUnavailable",
    "SeamError",
    "ToolOutcomeUnknown",
)


@dataclass(frozen=True, slots=True)
class CreateSession:
    """Everything SessionManager.create needs; the session id is chosen by the caller."""

    tenant_id: str
    harness_version: str
    runtime_name: str | None = None
    composition_id: str | None = None
    composition_digest: str | None = None
    scope_id: str | None = None
    principal_id: str | None = None
    # The session's memory binding request (supervisor.memory.binding.MemoryRequest).
    memory: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class CreateRequest:
    session_id: str
    spec: CreateSession


@dataclass(frozen=True, slots=True)
class ForkSpec:
    """Start a session as a copy of ``source_id``'s journal, up to ``at_seq``."""

    source_id: str
    at_seq: int | None = None


@dataclass(frozen=True, slots=True)
class ForkRequest:
    session_id: str
    spec: ForkSpec


@dataclass(frozen=True, slots=True)
class Message:
    command_id: str
    text: str
    trace_id: str | None = None


@dataclass(frozen=True, slots=True)
class Accepted:
    """submit_message result: prepared and about to run, or queued behind another turn."""

    command_id: str
    queued: bool


@dataclass(frozen=True, slots=True)
class ApprovalChoice:
    approval_id: str
    choice: str


@dataclass(frozen=True, slots=True)
class ApprovalRequest:
    session_id: str
    approval: ApprovalChoice


@dataclass(frozen=True, slots=True)
class Placement:
    """The worker-specific task queue of the worker holding the session's live runtime."""

    task_queue: str


@dataclass(frozen=True, slots=True)
class TurnRequest:
    session_id: str
    message: Message


class RetryMode(StrEnum):
    """How a tool call may be retried, from the manifest's recovery policy for its class."""

    RETRY = "retry"  # auto_resume: idempotent or pure, safe to run again
    ONCE = "once"  # anything else: at most once, an unknown outcome stops the turn


@dataclass(frozen=True, slots=True)
class GuardSettings:
    repetition: str
    oscillation: str
    stall_iterations: int


@dataclass(frozen=True, slots=True)
class CaptureConfig:
    """Automatic memory capture after a turn: flush a burst after quiet, or at max turns."""

    quiet_s: float
    max_turns: int


@dataclass(frozen=True, slots=True)
class CaptureRequest:
    session_id: str
    run_ids: list[str]


@dataclass(frozen=True, slots=True)
class RunPlan:
    """How a turn runs: stepped by the workflow, or as one activity (runtime-driven)."""

    run_id: str
    stepped: bool
    guard: GuardSettings | None = None
    max_steps: int = 0
    # Capture this turn into long-term memory (in a burst, after quiet).
    capture: CaptureConfig | None = None


@dataclass(frozen=True, slots=True)
class ApprovalNeeded:
    prompt: str
    options: list[str]
    timeout_s: float
    on_timeout: str


@dataclass(frozen=True, slots=True)
class PlannedCall:
    """A tool call the model asked for. Arguments stay in the journal; only a hash travels."""

    call_id: str
    name: str
    args_hash: str
    retry: RetryMode
    approval: ApprovalNeeded | None = None


@dataclass(frozen=True, slots=True)
class StepRequest:
    session_id: str
    run_id: str
    step: int


@dataclass(frozen=True, slots=True)
class StepResult:
    calls: list[PlannedCall]
    # Final: the model answered (stop) or policy ended the run (e.g. budget_exceeded).
    final: bool
    stop_reason: str = "stop"


@dataclass(frozen=True, slots=True)
class ToolRequest:
    session_id: str
    run_id: str
    call_id: str
    denied: bool = False


@dataclass(frozen=True, slots=True)
class ApprovalAsk:
    session_id: str
    run_id: str
    call_id: str
    tool_name: str
    needed: ApprovalNeeded


@dataclass(frozen=True, slots=True)
class ApprovalDecision:
    session_id: str
    approval_id: str
    choice: str
    timed_out: bool = False


LOST_WORKER = "the worker running this tool was lost"


class Op(StrEnum):
    """Session operations besides turns; each runs on the worker holding the session."""

    INVOKE_TOOL = "invoke_tool"
    COMPACT_MEMORY = "compact_memory"
    RECALL_MEMORY = "recall_memory"
    SPAWN_CHILD = "spawn_child"
    FORK_CHILD = "fork_child"
    START_BACKGROUND = "start_background"
    START_CONTINUABLE = "start_continuable"
    SEND_CHILD_MESSAGE = "send_child_message"
    INTERRUPT_CHILD = "interrupt_child"
    REHYDRATE_WORKSPACE = "rehydrate_workspace"


# Safe to run again if the first attempt's outcome is unknown; the rest run at most once.
RETRYABLE_OPS = frozenset({Op.INTERRUPT_CHILD, Op.REHYDRATE_WORKSPACE})


@dataclass(frozen=True, slots=True)
class SessionOp:
    """One operation; ``op_id`` dedups re-sends, ``args`` is the API request body.

    For INVOKE_TOOL, ``op_id`` is also the tool call id, so a lost call can be closed.
    """

    session_id: str
    op_id: str
    kind: Op
    tenant_id: str
    args: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class StartJob:
    """A journalled START_BACKGROUND op and the provider it was validated against."""

    op: SessionOp
    provider: str


@dataclass(frozen=True, slots=True)
class JobSpec:
    """A background subagent job; ``request`` is the SubagentRequest's fields."""

    session_id: str
    job_id: str
    run_id: str
    provider: str
    request: dict[str, Any]
    timing: Timing


@dataclass(frozen=True, slots=True)
class JobOutcome:
    job_id: str
    stop_reason: str  # ok | error | interrupted (or the provider's status)
    output: str | None = None


@dataclass(frozen=True, slots=True)
class JobEndRequest:
    spec: JobSpec
    stop_reason: str
    output: str | None = None


@dataclass(frozen=True, slots=True)
class OpResult:
    """The operation's HTTP response body; None for operations that answer 204."""

    body: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class LostCall:
    """A tool call that started but whose outcome is unknown (worker lost, or aborted)."""

    session_id: str
    # None: a direct tool invoke, closed through the tool outbox.
    run_id: str | None
    call_id: str
    reason: str = LOST_WORKER


@dataclass(frozen=True, slots=True)
class EndRunRequest:
    session_id: str
    run_id: str
    command_id: str
    stop_reason: str
    error_class: str | None = None
    detail: str | None = None
    # A loop guard trip: the session is closed (failed), not just the run.
    close_session: bool = False
    # A tool call that started but whose outcome is unknown (worker lost, or aborted).
    lost_call_id: str | None = None
    lost_reason: str = LOST_WORKER


# Session row statuses after which no turn can ever run again.
CLOSED_STATUSES = frozenset({"failed", "terminated"})


@dataclass(frozen=True, slots=True)
class TurnOutcome:
    command_id: str
    stop_reason: str
    last_seq: int
    # The session row's status after the turn: policy may have closed the session.
    session_status: str = "active"


@dataclass(slots=True)
class Carried:
    """State that survives continue-as-new. Kept small and bounded on purpose."""

    phase: Phase = Phase.CREATING
    placement: Placement | None = None
    pending: list[Message] = field(default_factory=list)
    # Bounded window of handled command ids so a re-sent message never runs twice,
    # even across continue-as-new (Temporal's update-id dedup is per run only).
    seen_commands: list[str] = field(default_factory=list)
    turns_completed: int = 0
    # Background jobs still running (their workflows are cancelled with the session).
    jobs: list[str] = field(default_factory=list)
    # Turns waiting to be captured into memory, and when the burst is due (epoch s).
    capture_runs: list[str] = field(default_factory=list)
    capture_due: float | None = None


@dataclass(frozen=True, slots=True)
class Timing:
    """Durability timeouts; per deployment, and shortened in tests."""

    # How long an activity may wait for the worker holding the session before
    # another worker adopts it. Long enough to ride out a busy worker.
    placement_timeout_s: float = 60.0
    # A turn that stops heartbeating this long is treated as a dead worker.
    heartbeat_timeout_s: float = 30.0
    # Upper bound for one turn, including approval waits inside a runtime.
    turn_timeout_s: float = 24 * 3600.0


@dataclass(frozen=True, slots=True)
class SessionInput:
    session_id: str
    create: CreateSession | None = None
    carried: Carried | None = None
    timing: Timing = field(default_factory=Timing)
    # Instead of ``create``: the new session is a fork of another.
    fork: ForkSpec | None = None


@dataclass(frozen=True, slots=True)
class SessionStatus:
    phase: Phase
    turns_completed: int
    queued: int
    running_command: str | None


@dataclass(frozen=True, slots=True)
class SessionResult:
    session_id: str
    phase: Phase
    turns_completed: int
