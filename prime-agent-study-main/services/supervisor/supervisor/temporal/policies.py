"""Every workflow step's time limit, retries, heartbeat and what happens when its worker dies.

One table, read by the workflows (``AgentSession``, ``SubagentJob``) when they schedule an
activity, and printed into the README (``python -m supervisor rollout policies``; a test keeps
the two in step). The durable-execution shape of the OpenAI Agents SDK on Temporal: the
workflow holds the plan, and each model call and tool call is its own step, retried on its
own terms, so a crash or a deploy costs at most the step in flight.

Changing a limit or a retry here is safe for runs in flight (Temporal does not replay them);
changing *which* steps a workflow schedules is not, and needs ``workflow.patched`` (see
``supervisor.temporal.rollout`` and the replay gate).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import timedelta
from enum import StrEnum
from types import MappingProxyType
from typing import Final

from temporalio.common import RetryPolicy

from supervisor.temporal.contracts import NON_RETRYABLE, Activity, Timing


class Limit(StrEnum):
    """How long one attempt of a step may take (``start_to_close``)."""

    SHORT = "short"
    MODEL_CALL = "model_call"
    TURN = "turn"  # Timing.turn_timeout_s: a tool or a whole turn, approvals included


class Retry(StrEnum):
    STANDARD = "standard"
    TURN = "turn"
    ONCE = "once"
    UNTIL_DONE = "until_done"


class Where(StrEnum):
    SESSION_WORKER = "session worker"  # the worker holding the session's runtime and sandbox
    ANY_WORKER = "any worker"  # the shared queue


SHORT: Final = timedelta(minutes=2)
MODEL_CALL: Final = timedelta(minutes=10)

RETRIES: Final[Mapping[Retry, RetryPolicy]] = MappingProxyType({
    # Transient trouble (a busy database, a flaky provider): 5 attempts over about a minute.
    Retry.STANDARD: RetryPolicy(
        initial_interval=timedelta(seconds=1),
        backoff_coefficient=2.0,
        maximum_interval=timedelta(seconds=30),
        maximum_attempts=5,
        non_retryable_error_types=list(NON_RETRYABLE),
    ),
    # A retried whole turn may repeat side effects; run_turn refuses to resend after tools ran.
    Retry.TURN: RetryPolicy(
        initial_interval=timedelta(seconds=2),
        maximum_attempts=3,
        non_retryable_error_types=list(NON_RETRYABLE),
    ),
    Retry.ONCE: RetryPolicy(maximum_attempts=1),
    # Bookkeeping that must land (a job's end): retried until it does.
    Retry.UNTIL_DONE: RetryPolicy(
        initial_interval=timedelta(seconds=1), maximum_interval=timedelta(seconds=30)
    ),
})

RETRY_TEXT: Final[Mapping[Retry, str]] = MappingProxyType({
    Retry.STANDARD: "5 attempts, 1 s doubling to 30 s",
    Retry.TURN: "3 attempts, 2 s apart",
    Retry.ONCE: "once",
    Retry.UNTIL_DONE: "until it lands, 1 s doubling to 30 s",
})


@dataclass(frozen=True, slots=True)
class StepPolicy:
    where: Where
    limit: Limit
    retry: Retry
    heartbeat: bool
    on_worker_lost: str
    does: str
    retry_note: str = ""
    """When the retry depends on the call (at-most-once tools and operations)."""

    def start_to_close(self, timing: Timing) -> timedelta:
        if self.limit is Limit.TURN:
            return timedelta(seconds=timing.turn_timeout_s)
        return MODEL_CALL if self.limit is Limit.MODEL_CALL else SHORT

    def heartbeat_timeout(self, timing: Timing) -> timedelta | None:
        return timedelta(seconds=timing.heartbeat_timeout_s) if self.heartbeat else None

    @property
    def retry_policy(self) -> RetryPolicy:
        return RETRIES[self.retry]


_ADOPTED = "placement times out; another worker adopts the session and runs it again"

POLICIES: Final[Mapping[Activity, StepPolicy]] = MappingProxyType({
    Activity.CREATE: StepPolicy(
        Where.ANY_WORKER, Limit.SHORT, Retry.STANDARD, False,
        "retried on another worker (creating is idempotent)", "Set up a new session",
    ),
    Activity.FORK: StepPolicy(
        Where.ANY_WORKER, Limit.SHORT, Retry.STANDARD, False,
        "retried on another worker", "Copy a session's journal into a new one",
    ),
    Activity.ADOPT: StepPolicy(
        Where.ANY_WORKER, Limit.SHORT, Retry.STANDARD, False,
        "retried on another worker", "Take over a session whose worker is gone",
    ),
    Activity.WORKER_BEATS: StepPolicy(
        Where.ANY_WORKER, Limit.SHORT, Retry.STANDARD, False,
        "retried on another worker; if it keeps failing, the session is adopted",
        "Before adopting a session: does its worker still heartbeat (busy, not gone)?",
    ),
    Activity.PREPARE_TURN: StepPolicy(
        Where.SESSION_WORKER, Limit.SHORT, Retry.STANDARD, False, _ADOPTED,
        "Check budget and recovery before a message runs; start the sandbox (with_run)",
    ),
    Activity.BEGIN_RUN: StepPolicy(
        Where.SESSION_WORKER, Limit.SHORT, Retry.STANDARD, False, _ADOPTED,
        "Open a run: journal the message, plan the steps",
    ),
    Activity.MODEL_STEP: StepPolicy(
        Where.ANY_WORKER, Limit.MODEL_CALL, Retry.STANDARD, True,
        "heartbeats stop; the model call is retried on another worker",
        "One model call (needs no sandbox, so any worker thinks)",
    ),
    Activity.COMPACT: StepPolicy(
        Where.SESSION_WORKER, Limit.MODEL_CALL, Retry.STANDARD, True, _ADOPTED,
        "Summarise the conversation inside a run so the next model step fits",
    ),
    Activity.CHECKPOINT: StepPolicy(
        Where.SESSION_WORKER, Limit.SHORT, Retry.STANDARD, False,
        "skipped; a lost checkpoint costs a restore, never the turn",
        "Save what the last tools changed in the sandbox (beside the model call)",
    ),
    Activity.PLACE_INPUTS: StepPolicy(
        Where.ANY_WORKER, Limit.SHORT, Retry.STANDARD, False,
        "retried on another worker (each message is keyed, so it lands once)",
        "Place steering messages in the running run before its next step",
    ),
    Activity.TOOL_BATCH: StepPolicy(
        Where.SESSION_WORKER, Limit.TURN, Retry.STANDARD, True,
        "re-run on the adopting worker (read-only calls; each one's result is kept)",
        "A round's read-only tool calls, run together in the sandbox",
    ),
    Activity.TOOL_CALL: StepPolicy(
        Where.SESSION_WORKER, Limit.TURN, Retry.STANDARD, True,
        "re-run on the adopting worker; an at-most-once tool is not: its call is answered "
        "as an unknown outcome and the run goes on (the model checks)",
        "One tool call in the sandbox",
        retry_note="once for at-most-once tools",
    ),
    Activity.REQUEST_APPROVAL: StepPolicy(
        Where.SESSION_WORKER, Limit.SHORT, Retry.STANDARD, False, _ADOPTED,
        "Ask a person (the wait itself is a durable timer, not a step)",
    ),
    Activity.RESOLVE_APPROVAL: StepPolicy(
        Where.SESSION_WORKER, Limit.SHORT, Retry.STANDARD, False, _ADOPTED,
        "Record an answer, or the timeout's choice",
    ),
    Activity.END_RUN: StepPolicy(
        Where.SESSION_WORKER, Limit.SHORT, Retry.STANDARD, False,
        "a call left half done is closed first, then the session is adopted",
        "Close a run: final answer, usage, why it stopped",
    ),
    Activity.RUN_TURN: StepPolicy(
        Where.SESSION_WORKER, Limit.TURN, Retry.TURN, True,
        "heartbeats stop; retried after adoption, but never resent once tools ran",
        "A whole runtime-driven turn (non-stepped harnesses)",
    ),
    Activity.RESPOND_APPROVAL: StepPolicy(
        Where.SESSION_WORKER, Limit.SHORT, Retry.STANDARD, False, _ADOPTED,
        "Deliver an answer to a runtime-driven turn",
    ),
    Activity.SESSION_OP: StepPolicy(
        Where.SESSION_WORKER, Limit.TURN, Retry.STANDARD, True,
        "an operation with side effects is not re-run: a tool invoke is closed as lost and "
        "reported as outcome unknown",
        "Tool invoke, memory, subagents, workspace",
        retry_note="once for operations with side effects",
    ),
    Activity.CLOSE_LOST_CALL: StepPolicy(
        Where.ANY_WORKER, Limit.SHORT, Retry.STANDARD, False,
        "retried on another worker (journal only)", "Close a call whose worker died",
    ),
    Activity.TERMINATE: StepPolicy(
        Where.SESSION_WORKER, Limit.SHORT, Retry.STANDARD, False, _ADOPTED,
        "End the session: stop its runtime and sandbox",
    ),
    Activity.CAPTURE_MEMORY: StepPolicy(
        Where.ANY_WORKER, Limit.MODEL_CALL, Retry.STANDARD, True,
        "retried elsewhere; a burst that keeps failing is dropped (best effort)",
        "Save durable facts from a burst of turns",
    ),
    Activity.RUN_JOB: StepPolicy(
        Where.ANY_WORKER, Limit.TURN, Retry.STANDARD, True,
        "heartbeats stop; the job is retried on another worker, never lost",
        "A background subagent job's work",
    ),
    Activity.END_JOB: StepPolicy(
        Where.ANY_WORKER, Limit.SHORT, Retry.UNTIL_DONE, False,
        "retried until exactly one end reaches the session's journal",
        "Record how a background job ended",
    ),
})


def policy(activity: Activity) -> StepPolicy:
    return POLICIES[activity]


def _limit_text(limit: Limit, timing: Timing) -> str:
    if limit is Limit.TURN:
        return f"turn limit ({timing.turn_timeout_s / 3600:g} h)"
    return f"{int((MODEL_CALL if limit is Limit.MODEL_CALL else SHORT).total_seconds() // 60)} min"


def policy_table(timing: Timing | None = None) -> str:
    """The table as Markdown (the README's copy is checked against this)."""
    t = timing or Timing()
    rows = [
        "| Step | Does | Where | Time limit | Heartbeat | Retries | If its worker dies |",
        "|---|---|---|---|---|---|---|",
    ]
    for activity, p in POLICIES.items():
        retries = RETRY_TEXT[p.retry] + (f" ({p.retry_note})" if p.retry_note else "")
        beat = f"every {t.heartbeat_timeout_s:g} s" if p.heartbeat else "no"
        rows.append(
            f"| `{activity.value}` | {p.does} | {p.where.value} | {_limit_text(p.limit, t)} | "
            f"{beat} | {retries} | {p.on_worker_lost} |"
        )
    return "\n".join(rows)
