"""Background one-shot subagent jobs + settlement notices.

Foreground spawn/fork stay synchronous on the tool thread. Background work
lives here: a daemon thread, a per-job Interrupt, and a notice injected into
the parent via next_turn (mid-run) or follow_up (idle).
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal
from uuid import uuid4

from ..agent.interrupt import Interrupt
from ..types import Message, StopReason, UserMessage
from . import SubagentError, SubagentRunResult, assert_can_spawn
from .seed import completed_turn_prefix

if TYPE_CHECKING:
    from ..agent.agent import Agent

JobStatus = Literal["running", "completed", "failed", "cancelled"]
SpawnMode = Literal["spawn", "fork"]


@dataclass
class SubagentJob:
    """One background child run owned by the parent registry."""

    id: str
    mode: SpawnMode
    status: JobStatus = "running"
    description: str = ""
    result: SubagentRunResult | None = None
    error: str | None = None
    interrupt: Interrupt = field(default_factory=Interrupt)
    thread: threading.Thread | None = None
    # Fork seed captured at ack time (before the parent continues).
    seed: list[Message] | None = None


def format_settlement_notice(job: SubagentJob) -> str:
    """Text injected into the parent transcript when a background job ends."""
    if job.result is not None:
        reason = job.result.stop_reason.value
        closing = (job.result.output or "").strip()
    elif job.error:
        reason = "failed"
        closing = job.error.strip()
    else:
        reason = job.status
        closing = ""
    if not closing:
        closing = "It left no closing message."
    return (
        f"Background subagent job {job.id} finished ({reason}).\n"
        f"Its closing message:\n{closing}"
    )


def _status_for_result(result: SubagentRunResult) -> JobStatus:
    if result.stop_reason is StopReason.INTERRUPTED:
        return "cancelled"
    if result.stop_reason is StopReason.DONE:
        return "completed"
    return "failed"


class SubagentJobRegistry:
    """Tracks background subagent runs for one parent Agent."""

    def __init__(self, parent: Agent) -> None:
        self._parent = parent
        self._lock = threading.Lock()
        self._jobs: dict[str, SubagentJob] = {}

    def get(self, job_id: str) -> SubagentJob | None:
        with self._lock:
            return self._jobs.get(job_id)

    def start_spawn(
        self,
        prompt: str,
        *,
        description: str = "",
        parent_depth: int = 0,
    ) -> str:
        return self._start(
            "spawn",
            prompt,
            description=description,
            parent_depth=parent_depth,
        )

    def start_fork(
        self,
        prompt: str,
        *,
        description: str = "",
        parent_depth: int = 0,
    ) -> str:
        return self._start(
            "fork",
            prompt,
            description=description,
            parent_depth=parent_depth,
        )

    def output_text(self, job_id: str) -> tuple[bool, str, dict[str, Any]]:
        """Return (ok, text, details) for the job_output tool."""
        job = self.get(job_id)
        if job is None:
            return False, f"no background subagent job named {job_id!r}", {
                "code": "NOT_FOUND",
            }
        details: dict[str, Any] = {
            "job_id": job.id,
            "status": job.status,
            "mode": job.mode,
            "description": job.description,
        }
        if job.status == "running":
            return True, f"background subagent job {job.id} is still running", details
        if job.result is not None:
            details["stop_reason"] = job.result.stop_reason.value
            closing = (job.result.output or "").strip() or "It left no closing message."
            text = (
                f"background subagent job {job.id} {job.status} "
                f"({job.result.stop_reason.value}).\n"
                f"Closing message:\n{closing}"
            )
            return True, text, details
        if job.error:
            details["error"] = job.error
            return True, (
                f"background subagent job {job.id} failed.\n{job.error}"
            ), details
        return True, f"background subagent job {job.id} ended ({job.status})", details

    def kill(self, job_id: str) -> tuple[bool, str, dict[str, Any]]:
        """Signal the job's Interrupt; status updates when the child exits."""
        job = self.get(job_id)
        if job is None:
            return False, f"no background subagent job named {job_id!r}", {
                "code": "NOT_FOUND",
            }
        details: dict[str, Any] = {
            "job_id": job.id,
            "status": job.status,
            "mode": job.mode,
        }
        if job.status != "running":
            return True, (
                f"background subagent job {job.id} is already {job.status}"
            ), details
        job.interrupt.set()
        return True, (
            f"sent cancel to background subagent job {job.id}"
        ), details

    def abort_all(self) -> bool:
        """Interrupt every running job. Returns True if any were signaled."""
        with self._lock:
            jobs = list(self._jobs.values())
        signaled = False
        for job in jobs:
            if job.status == "running":
                job.interrupt.set()
                signaled = True
        return signaled

    def _start(
        self,
        mode: SpawnMode,
        prompt: str,
        *,
        description: str,
        parent_depth: int,
    ) -> str:
        text = (prompt or "").strip()
        if not text:
            raise SubagentError(
                "subagent requires a non-empty `prompt`",
                "BAD_ARGS",
            )
        assert_can_spawn(parent_depth, self._parent.subagents.max_depth)

        seed: list[Message] | None = None
        if mode == "fork":
            # Snapshot now (ack time), while the parent's open tool turn is
            # still open — not later on the worker thread.
            seed = completed_turn_prefix(self._parent.state.messages) or None

        job = SubagentJob(
            id=uuid4().hex[:12],
            mode=mode,
            description=(description or "").strip(),
            seed=seed,
        )
        with self._lock:
            self._jobs[job.id] = job

        thread = threading.Thread(
            target=self._run_job,
            args=(job, text, parent_depth),
            name=f"githi-subagent-{job.id}",
            daemon=True,
        )
        job.thread = thread
        thread.start()
        return job.id

    def _run_job(self, job: SubagentJob, prompt: str, parent_depth: int) -> None:
        service = self._parent.subagents
        try:
            if job.mode == "spawn":
                result = service.start_one_shot(
                    prompt,
                    description=job.description,
                    parent_depth=parent_depth,
                    interrupt=job.interrupt,
                    run_id=job.id,
                )
            else:
                result = service.start_fork(
                    prompt,
                    description=job.description,
                    parent_depth=parent_depth,
                    interrupt=job.interrupt,
                    run_id=job.id,
                    seed=job.seed,
                )
            with self._lock:
                job.result = result
                job.status = _status_for_result(result)
        except Exception as exc:  # noqa: BLE001 - settle even on unexpected errors
            with self._lock:
                job.error = str(exc)
                job.status = "failed"
        finally:
            self._deliver_settlement(job)

    def _deliver_settlement(self, job: SubagentJob) -> None:
        notice = UserMessage(format_settlement_notice(job))
        self._parent.deliver_background_notice(notice)
