"""Subagent tools, as githi offers them (``githi/src/githi/tools``).

Offered when the session's harness pins a ``subagents`` block and the session is shallower
than its ``max_depth`` (a child is one level deeper than the session that started it):

* ``subagent`` starts a fresh child session with the task; ``subagent_fork`` starts one that
  carries this conversation's completed turns. By default the call waits for the child's
  answer; with ``run_in_background`` (when the harness allows background subagents) it is
  answered at once with the child's id as a job id, and the session is told when the child
  settles (a ``SubagentNotice``). These two are run by the session's workflow, not here: the
  call's plan says so (``PlannedCall.delegate``) and ``supervisor.session.children`` says how.
* ``job_output`` reads a background subagent's status and closing message; ``job_kill``
  stops it (its settlement notice still comes). A child is never offered these.

``subagent_result`` reads a job of the transport-based background subagents that ran before
subagents were sessions, and is answered only for those (no longer offered).

Depth, background permission and how many background subagents may run at once all come
from what the session pinned; nothing here is chosen by the model beyond the task itself.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final

from pydantic import ValidationError
from sqlalchemy import func, select
from temporalio import activity

from supervisor.agent.events import (
    BackgroundResults,
    JobEnd,
    JobStart,
    SubagentEnd,
    SubagentStart,
    ToolEnd,
)
from supervisor.db.models import SessionRow
from supervisor.temporal.contracts import CLOSED_STATUSES, MAX_MESSAGE_CHARS, workflow_id
from supervisor.tools import InvalidArguments, ToolArgs, ToolError, json_schema

if TYPE_CHECKING:
    from supervisor.journal.reader import JournalReader
    from supervisor.session import SessionManager

__all__ = [
    "JOB_KILL",
    "JOB_OUTPUT",
    "MAX_BACKGROUND_JOBS",
    "SPAWN_MODES",
    "SUBAGENT",
    "SUBAGENT_FORK",
    "SUBAGENT_RESULT",
    "SUBAGENT_TOOLS",
    "Outcome",
    "SubagentTools",
    "Task",
    "announcement",
    "delegated",
    "render",
    "settlement",
    "task",
]

SUBAGENT: Final = "subagent"
SUBAGENT_FORK: Final = "subagent_fork"
JOB_OUTPUT: Final = "job_output"
JOB_KILL: Final = "job_kill"
SUBAGENT_RESULT: Final = "subagent_result"
SUBAGENT_TOOLS: Final = frozenset({SUBAGENT, SUBAGENT_FORK, JOB_OUTPUT, JOB_KILL, SUBAGENT_RESULT})
SPAWN_MODES: Final = {SUBAGENT: "spawn", SUBAGENT_FORK: "fork"}
"""The tools that start a child session, and how each starts it."""

MAX_BACKGROUND_JOBS: Final = 8
"""Background subagents a session may have running at once. Each spends on the session's
budget until it ends, so the model cannot fan out without bound; one more is refused, and
the model reads why."""

SUBAGENT_DESCRIPTION: Final = (
    "Delegate a self-contained task to a fresh subagent with its own conversation and your "
    "tools, working in your files. The child does not see this conversation: put every file "
    "path, constraint and acceptance criterion in `prompt`. By default waits for the child and "
    "returns only its final answer. Prefer this for isolated research or implementation work "
    "you can summarize; a decision only the user can make stays with you."
)
FORK_DESCRIPTION: Final = (
    "Delegate a follow-up task to a subagent that already sees this conversation's completed "
    "turns (not the current one). Use it when the child needs that context; put only the new "
    "instructions in `prompt`. For a fully isolated task use `subagent` instead. By default "
    "waits for the child and returns only its final answer."
)
BACKGROUND_DESCRIPTION: Final = (
    "Start the child and return at once with a job id; you are told when it finishes (or "
    "read it with `job_output`)"
)
JOB_OUTPUT_DESCRIPTION: Final = (
    "Read the status and closing message of a background subagent started with "
    "`run_in_background`. Use it when you need the result and have not been told it finished, "
    "or to check whether it is still running."
)
JOB_KILL_DESCRIPTION: Final = (
    "Stop a running background subagent by its job id. Only that child is stopped; you keep "
    "working, and you are still told when it ends. Does nothing if it already finished."
)


class SubagentArgs(ToolArgs):
    prompt: str
    """Full standalone instructions for the child: it does not see this conversation"""

    description: str = ""
    """A few words naming the task (for status and logs, not the child's prompt)"""

    run_in_background: bool = False
    """Start the child and return at once with a job id"""


class ForkArgs(ToolArgs):
    prompt: str
    """Additional instructions for the child; this conversation's completed turns are already in its context"""

    description: str = ""
    """A few words naming the task (for status and logs, not the child's prompt)"""

    run_in_background: bool = False
    """Start the child and return at once with a job id"""


class JobArgs(ToolArgs):
    job_id: str
    """The id a background subagent call returned"""


class DepthExceeded(ToolError):
    code = "DEPTH_EXCEEDED"


class TooManyJobs(ToolError):
    code = "TOO_MANY_JOBS"


class NotFound(ToolError):
    code = "NOT_FOUND"


class Replanned(ToolError):
    code = "ASK_AGAIN"


@dataclass(frozen=True, slots=True)
class Outcome:
    payload: Any
    is_error: bool = False


@dataclass(frozen=True, slots=True)
class Task:
    """A child session's task, as a ``subagent``/``subagent_fork`` call asked for it."""

    mode: str  # spawn | fork
    prompt: str
    label: str | None
    background: bool = False


def delegated(name: str, raw: dict[str, Any]) -> tuple[str, bool] | None:
    """How a call starts a child session the workflow runs: (spawn or fork, in the
    background); None when it is no such call."""
    mode = SPAWN_MODES.get(name)
    if mode is None:
        return None
    return mode, raw.get("run_in_background") in (True, "true", "True", 1)


def task(name: str, raw: dict[str, Any], *, depth: int, max_depth: int, background_allowed: bool) -> Task:
    """The task a subagent call asks for; ``ToolError`` the model reads when the call cannot
    start one (its arguments, a child past the harness's depth, background not allowed)."""
    args: SubagentArgs | ForkArgs = _parse(ForkArgs, raw) if name == SUBAGENT_FORK else _parse(SubagentArgs, raw)
    prompt = args.prompt.strip()
    if not prompt:
        raise InvalidArguments(f"{name} requires a non-empty `prompt`")
    if len(prompt) > MAX_MESSAGE_CHARS:
        raise InvalidArguments(
            f"the prompt is {len(prompt):,} characters; a subagent's task may be at most "
            f"{MAX_MESSAGE_CHARS:,}: put long material in a file and name it in the prompt"
        )
    if depth + 1 > max_depth:
        raise DepthExceeded(
            f"delegation depth cap exceeded: the child would be at depth {depth + 1} but "
            f"max_depth is {max_depth}"
        )
    if args.run_in_background and not background_allowed:
        raise InvalidArguments("this agent may not start background subagents")
    return Task(SPAWN_MODES[name], prompt, args.description.strip() or None, args.run_in_background)


def settlement(job_id: str, status: str, text: str) -> str:
    """What the session's model is told when a background subagent settles (githi's
    ``format_settlement_notice``)."""
    closing = text.strip() or "It left no closing message."
    return f"Background subagent job {job_id} finished ({status}).\nIts closing message:\n{closing}"


class SubagentTools:
    def __init__(self, manager: SessionManager, reader: JournalReader) -> None:
        self._manager = manager
        self._reader = reader

    async def schemas(self, session_id: str) -> list[dict[str, Any]]:
        """The subagent tools this session may use: none unless it pinned subagents and is
        shallower than their ``max_depth`` (githi's child offers them while it may spawn)."""
        row = await self._manager._get_session_row(session_id)
        spec = self._manager._pinned_manifest(row).subagents
        if spec is None or spec.max_depth <= row.subagent_depth:
            return []
        spawn, fork = json_schema(SubagentArgs), json_schema(ForkArgs)
        for schema in (spawn, fork):
            if spec.allow_background:
                schema["properties"]["run_in_background"]["description"] = BACKGROUND_DESCRIPTION
            else:
                del schema["properties"]["run_in_background"]
        tools = [_function(SUBAGENT, SUBAGENT_DESCRIPTION, spawn), _function(SUBAGENT_FORK, FORK_DESCRIPTION, fork)]
        if spec.allow_background:
            tools += [
                _function(JOB_OUTPUT, JOB_OUTPUT_DESCRIPTION, json_schema(JobArgs)),
                _function(JOB_KILL, JOB_KILL_DESCRIPTION, json_schema(JobArgs)),
            ]
        return tools

    async def call(
        self, session_id: str, run_id: str, call_id: str, name: str, raw: dict[str, Any]
    ) -> Outcome:
        try:
            if name == JOB_OUTPUT:
                return await self.job_output(session_id, _parse(JobArgs, raw).job_id)
            if name == JOB_KILL:
                return await self.job_kill(session_id, _parse(JobArgs, raw).job_id)
            if name == SUBAGENT_RESULT:
                return await self._result(session_id, _parse(JobArgs, raw).job_id)
            # A subagent call planned by a worker from before subagents were sessions: its
            # workflow ran it as a tool. The model asks again and the new plan starts a child.
            raise Replanned(f"the platform was upgraded while this call waited; call {name} again")
        except ToolError as exc:
            return Outcome({"error": str(exc), "code": exc.code}, is_error=True)

    async def running_in_background(self, session_id: str) -> int:
        """Background subagents of the session still open."""
        async with self._manager._db() as db:
            count = await db.scalar(
                select(func.count())
                .select_from(SessionRow)
                .where(
                    SessionRow.parent_session_id == session_id,
                    SessionRow.subagent_kind == "background",
                    SessionRow.status.not_in(CLOSED_STATUSES),
                )
            )
        return int(count or 0)

    async def job_output(self, session_id: str, job_id: str) -> Outcome:
        """githi's ``job_output``: the job's status (running, completed, failed, cancelled)
        and, once it ended, its closing message."""
        start, end = await self._job(session_id, job_id)
        details = {"job_id": job_id, "mode": start.mode, "description": start.label or ""}
        if end is None:
            return Outcome({**details, "status": "running", "text": f"background subagent job {job_id} is still running"})
        return Outcome({**details, "status": end.stop_reason, "text": end.output or ""})

    async def job_kill(self, session_id: str, job_id: str) -> Outcome:
        """githi's ``job_kill``: stop the job's run; it settles (and the session is told) as
        any job does."""
        _, end = await self._job(session_id, job_id)
        if end is not None:
            return Outcome({"job_id": job_id, "status": end.stop_reason, "text": f"background subagent job {job_id} is already {end.stop_reason}"})
        await activity.client().get_workflow_handle(workflow_id(job_id)).signal("abort")
        return Outcome({"job_id": job_id, "status": "running", "text": f"sent cancel to background subagent job {job_id}"})

    async def _job(self, session_id: str, job_id: str) -> tuple[SubagentStart, SubagentEnd | None]:
        """The start and (once it settled) the end of the session's background child ``job_id``."""
        async with self._manager._db() as db:
            child = await db.get(SessionRow, job_id)
        if child is None or child.parent_session_id != session_id or child.subagent_kind != "background":
            raise NotFound(f"no background subagent job named {job_id!r}")
        start: SubagentStart | None = None
        end: SubagentEnd | None = None
        for envelope in await self._reader.read(session_id):
            match envelope.payload:
                case SubagentStart(child_session_id=started) as s if started == job_id:
                    start = s
                case SubagentEnd(run_id=run) as e if start is not None and run == start.run_id:
                    end = e
        if start is None:
            raise NotFound(f"no background subagent job named {job_id!r}")
        return start, end

    async def unannounced(self, session_id: str) -> list[dict[str, Any]]:
        """Finished jobs of the transport-based background subagents (from before subagents
        were sessions) the model has neither been told about nor asked for."""
        jobs: list[str] = []
        ended: dict[str, JobEnd] = {}
        announced: set[str] = set()
        for envelope in await self._reader.read(session_id):
            match envelope.payload:
                case JobStart(job_id=job_id, kind="subagent"):
                    jobs.append(job_id)
                case JobEnd(job_id=job_id) as end:
                    ended[job_id] = end
                case BackgroundResults(results=results):
                    announced.update(str(r["job_id"]) for r in results)
                case ToolEnd(tool_name=name, result=result) if name == SUBAGENT_RESULT:
                    payload = (result or {}).get("payload") or {}
                    if payload.get("status") not in (None, "running"):
                        announced.add(str(payload.get("job_id")))
        return [
            {"job_id": job_id, "status": ended[job_id].stop_reason, "text": ended[job_id].output}
            for job_id in jobs
            if job_id in ended and job_id not in announced
        ]

    async def _result(self, session_id: str, job_id: str) -> Outcome:
        """A transport-based job's outcome, read from the journal."""
        started = ended = None
        for envelope in await self._reader.read(session_id):
            event = envelope.payload
            if isinstance(event, JobStart) and event.job_id == job_id:
                started = event
            elif isinstance(event, JobEnd) and event.job_id == job_id:
                ended = event
        if started is None and ended is None:
            raise NotFound(f"no background subagent with job_id {job_id!r}")
        if ended is None:
            return Outcome({"job_id": job_id, "status": "running"})
        return Outcome(
            {"job_id": job_id, "status": ended.stop_reason, "text": ended.output},
            is_error=ended.stop_reason not in ("ok", "stop"),
        )


def _parse[A: ToolArgs](model: type[A], raw: dict[str, Any]) -> A:
    try:
        return model.model_validate(raw)
    except ValidationError as exc:
        raise InvalidArguments(f"invalid arguments: {exc.errors()[0]['msg']}") from None


def _function(name: str, description: str, parameters: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {"name": name, "description": description, "parameters": parameters},
    }


def render(outcome: Outcome) -> dict[str, Any]:
    """A subagent tool's result in the same shape as a catalog tool's."""
    error = outcome.payload.get("error") if outcome.is_error else None
    return {
        "ok": not outcome.is_error,
        "payload": json.loads(json.dumps(outcome.payload, default=str)),
        "error": error,
        "code": outcome.payload.get("code") if outcome.is_error else None,
    }


def announcement(event: BackgroundResults) -> str:
    """How finished transport-based background jobs are put to the model."""
    lines = ["Background subagents you started have finished:"]
    for result in event.results:
        text = (result.get("text") or "").strip() or "(no output)"
        lines.append(f"- {result['job_id']} ({result['status']}): {text}")
    return "\n".join(lines)
