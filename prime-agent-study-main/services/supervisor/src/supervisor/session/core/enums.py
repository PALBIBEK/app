"""Session enumerations for state machines and modes.

All enums inherit from StrEnum for native JSON serialization
and human-readable string values.
"""

from __future__ import annotations

from enum import StrEnum

__all__ = [
    "ChildActivity",
    "JobActivity",
    "ResumeState",
    "SessionMode",
    "SessionStatus",
    "mode_for_harness",
    "mode_for_manifest",
]


class SessionMode(StrEnum):
    """Execution mode for a session.

    Determines how session state is managed across turns:
    - STICKY: Persistent state maintained between turns (sandbox-based)
    - STATELESS_PER_TURN: Fresh state for each user message
    """

    STICKY = "sticky"
    STATELESS_PER_TURN = "stateless_per_turn"


class ResumeState(StrEnum):
    """Result of session recovery decision.

    Describes the resumability state after restart or cold-load:
    - WARM: Session still active in memory, no recovery needed
    - COLD: Safe to resume from journal replay
    - NEEDS_REVIEW: Human review required (unpaired tools/compactions)
    - FAILED: Unrecoverable tool failure per recovery policy
    - UNRECOVERABLE: Sandbox state lost, cannot resume
    """

    WARM = "warm"
    COLD = "cold"
    NEEDS_REVIEW = "needs_review"
    FAILED = "failed"
    UNRECOVERABLE = "unrecoverable"


class SessionStatus(StrEnum):
    """Lifecycle status of a session in the database.

    Tracks the current operational state:
    - ACTIVE: Session is running normally
    - AWAITING_APPROVAL: Blocked waiting for human approval
    - BUDGET_EXCEEDED: Cost limit reached, session halted
    - TERMINATED: Session ended gracefully
    - FAILED: Session ended due to unrecoverable error
    """

    ACTIVE = "active"
    AWAITING_APPROVAL = "awaiting_approval"
    BUDGET_EXCEEDED = "budget_exceeded"
    TERMINATED = "terminated"
    FAILED = "failed"


class ChildActivity(StrEnum):
    """Activity state of a child session in the tree.

    Tracks the lifecycle of spawned subagents:
    - RUNNING: Child is actively executing
    - IDLE: Child exists but not processing
    - READY: Child is ready to receive work
    - INACTIVE: Child is paused or suspended
    - DONE: Child has completed execution
    """

    RUNNING = "running"
    IDLE = "idle"
    READY = "ready"
    INACTIVE = "inactive"
    DONE = "done"


class JobActivity(StrEnum):
    """Activity state of a background job.

    Tracks job execution:
    - RUNNING: Job is actively executing
    - DONE: Job has completed (success or failure)
    """

    RUNNING = "running"
    DONE = "done"


# =============================================================================
# Mode Helper Functions
# =============================================================================


def mode_for_harness(*, requires_sandbox: bool) -> SessionMode:
    """Legacy default mapping from sandbox requirement.

    Args:
        requires_sandbox: Whether the harness requires a sandbox

    Returns:
        SessionMode based on sandbox requirement
    """
    if requires_sandbox:
        return SessionMode.STICKY
    return SessionMode.STATELESS_PER_TURN


def mode_for_manifest(manifest: object) -> SessionMode:
    """Determine mode from harness manifest.

    Mode comes from the harness manifest, not a caller flag.

    Args:
        manifest: Harness manifest object

    Returns:
        SessionMode from manifest or inferred from sandbox requirement
    """
    mode = getattr(manifest, "mode", None)
    if mode == "sticky":
        return SessionMode.STICKY
    if mode == "stateless":
        return SessionMode.STATELESS_PER_TURN
    return mode_for_harness(requires_sandbox=bool(getattr(manifest, "requires_sandbox", False)))
