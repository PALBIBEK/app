"""Types shared by the API, the session workflow and its activities.

Imported inside the workflow sandbox, so this module must stay free of I/O and
of anything non-deterministic. Every type is small: conversation content lives
in the journal, never in workflow history (Temporal payloads are capped at 2 MB).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

WORKFLOW_ID_PREFIX = "session/"


def workflow_id(session_id: str) -> str:
    return f"{WORKFLOW_ID_PREFIX}{session_id}"


class Activity(StrEnum):
    """Activity names. The workflow calls activities by name so it never imports their code."""

    CREATE = "session.create"
    ADOPT = "session.adopt"
    PREPARE_TURN = "session.prepare_turn"
    RUN_TURN = "session.run_turn"
    RESPOND_APPROVAL = "session.respond_approval"
    TERMINATE = "session.terminate"


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


@dataclass(frozen=True, slots=True)
class CreateRequest:
    session_id: str
    spec: CreateSession


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
