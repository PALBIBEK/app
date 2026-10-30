"""Subagent tools, as githi offers them (``githi/src/githi/tools``).

Offered when the session's harness pins a ``subagents`` block and the session is shallower
than its ``max_depth`` (a child is one level deeper than the session that started it):

* ``subagent`` starts a fresh child session with the task and waits for its answer;
* ``subagent_fork`` does the same with a child that carries this conversation's completed
  turns. Both are run by the session's workflow, not here: the call's plan says so
  (``PlannedCall.delegate``) and ``supervisor.session.children`` says how.

With ``background`` (when the manifest allows it) ``subagent`` instead runs the task as the
session's durable ``SubagentJob`` workflow on its pinned subagent transport, and
``subagent_result`` reads such a job's outcome from the journal. A background job that
finishes is announced without being asked: the next run starts with a ``BackgroundResults``
event listing every finished job the model has not yet seen.

Depth, background permission and provider all come from what the session pinned; nothing
here is chosen by the model beyond the task itself.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final

from pydantic import ValidationError
from temporalio import activity

from supervisor.agent.events import BackgroundResults, JobEnd, JobStart, ToolEnd
from supervisor.hands.keys import hand_key
from supervisor.hands.leases import Borrower
from supervisor.seams import SubagentRequest
from supervisor.temporal.contracts import (
    MAX_MESSAGE_CHARS,
    START_JOB_SIGNAL,
    Op,
    SessionOp,
    StartJob,
    job_ids,
    workflow_id,
)
from supervisor.tools import InvalidArguments, ToolArgs, ToolError, json_schema

if TYPE_CHECKING:
    from supervisor.journal.reader import JournalReader
    from supervisor.session import SessionManager

__all__ = [
    "MAX_BACKGROUND_JOBS",
    "SUBAGENT",
    "SUBAGENT_FORK",
    "SUBAGENT_RESULT",
    "SUBAGENT_TOOLS",
    "SubagentTools",
    "Task",
    "announcement",
    "foreground",
    "render",
    "task",
]

SUBAGENT: Final = "subagent"
SUBAGENT_FORK: Final = "subagent_fork"
SUBAGENT_RESULT: Final = "subagent_result"
SUBAGENT_TOOLS: Final = frozenset({SUBAGENT, SUBAGENT_FORK, SUBAGENT_RESULT})
SPAWN_MODES: Final = {SUBAGENT: "spawn", SUBAGENT_FORK: "fork"}
"""The tools that start a child session, and how each starts it."""

SUBAGENT_DESCRIPTION: Final = (
    "Delegate a self-contained task to a fresh subagent with its own conversation and your "
    "tools, working in your files. The child does not see this conversation: put every file "
    "path, constraint and acceptance criterion in `prompt`. Waits for the child and returns "
    "only its final answer. Prefer this for isolated research or implementation work you can "
    "summarize; a decision only the user can make stays with you."
)
FORK_DESCRIPTION: Final = (
    "Delegate a follow-up task to a subagent that already sees this conversation's completed "
    "turns (not the current one). Use it when the child needs that context; put only the new "
    "instructions in `prompt`. For a fully isolated task use `subagent` instead. Waits for the "
    "child and returns only its final answer."
)

MAX_BACKGROUND_JOBS: Final = 8
"""Background subagents a session may have running at once. Each spends on the session's
model until it ends, so the model cannot fan out without bound; one more is refused, and
the model reads why."""


class SubagentArgs(ToolArgs):
    prompt: str
    """The task for the subagent, with everything it needs: it does not see this conversation"""

    description: str = ""
    """A few words naming the task (for status and logs, not the child's prompt)"""

    background: bool = False
    """Start it and return at once with a job_id; read its outcome later with subagent_result"""

    hand: str = ""
    """Background only: lend it one of your sandboxes to work in; you get it back when it ends"""


class HandLent(ToolError):
    code = "HAND_LENT"


class TooManyJobs(ToolError):
    code = "TOO_MANY_JOBS"


class Replanned(ToolError):
    code = "ASK_AGAIN"


class ForkArgs(ToolArgs):
    prompt: str
    """Additional instructions for the child; this conversation's completed turns are already in its context"""

    description: str = ""
    """A few words naming the task (for status and logs, not the child's prompt)"""


class SubagentResultArgs(ToolArgs):
    job_id: str
    """The job_id a background subagent call returned"""


class DepthExceeded(ToolError):
    code = "DEPTH_EXCEEDED"


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


def foreground(name: str, raw: dict[str, Any]) -> str | None:
    """How a call starts a child session the workflow runs ("spawn" or "fork"), or None when
    it is no such call (a background ``subagent`` runs as a job, through ``call``)."""
    mode = SPAWN_MODES.get(name)
    if mode == "spawn" and raw.get("background") in (True, "true", "True"):
        return None
    return mode


def task(name: str, raw: dict[str, Any], *, depth: int, max_depth: int) -> Task:
    """The task a foreground subagent call asks for; ``ToolError`` the model reads when the
    call cannot start one (its arguments, or a child past the harness's depth)."""
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
    return Task(SPAWN_MODES[name], prompt, args.description.strip() or None)


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
        spawn = json_schema(SubagentArgs)
        # Background jobs run on the pinned transport, as the session itself (not a child).
        background = spec.allow_background and row.root_session_id is None and self._has_transport(session_id, row)
        if not background:
            del spawn["properties"]["background"]
        hands = self._manager.hand_names(session_id)
        if not background or len(hands) <= 1:
            del spawn["properties"]["hand"]
        else:
            spawn["properties"]["hand"]["enum"] = hands
        tools = [
            _function(SUBAGENT, SUBAGENT_DESCRIPTION, spawn),
            _function(SUBAGENT_FORK, FORK_DESCRIPTION, json_schema(ForkArgs)),
        ]
        if background:
            tools.append(
                _function(
                    SUBAGENT_RESULT,
                    "Get the outcome of a background subagent, or that it is still running.",
                    json_schema(SubagentResultArgs),
                )
            )
        return tools

    def _has_transport(self, session_id: str, row: Any) -> bool:
        try:
            self._manager.subagent_provider(session_id, row, None)
        except Exception:  # noqa: BLE001 — no transport, or none with a provider
            return False
        return True

    async def call(
        self, session_id: str, run_id: str, call_id: str, name: str, raw: dict[str, Any]
    ) -> Outcome:
        try:
            if name == SUBAGENT_RESULT:
                return await self._result(session_id, _parse(SubagentResultArgs, raw))
            if foreground(name, raw) is not None:
                # Planned by a worker from before subagents were sessions: its workflow ran
                # it as a tool. The model asks again and the new plan starts a child.
                raise Replanned(f"the platform was upgraded while this call waited; call {name} again")
            return await self._delegate(session_id, run_id, call_id, _parse(SubagentArgs, raw))
        except ToolError as exc:
            return Outcome({"error": str(exc), "code": exc.code}, is_error=True)
        except Exception as exc:  # noqa: BLE001 — depth, capability and provider errors are the model's to read
            code = (
                getattr(exc, "code", None) or getattr(exc, "error_code", None) or type(exc).__name__
            )
            return Outcome({"error": str(exc), "code": code}, is_error=True)

    async def _delegate(
        self, session_id: str, run_id: str, call_id: str, args: SubagentArgs
    ) -> Outcome:
        """A background subagent: a durable job on the session's pinned transport."""
        row = await self._manager._get_session_row(session_id)
        spec = self._manager._pinned_manifest(row).subagents
        if not (spec is not None and spec.allow_background):
            raise InvalidArguments("this agent may not start background subagents")
        request = SubagentRequest(prompt=args.prompt, description=args.description, mode="background")
        op_id = f"{run_id}:{call_id}"
        job_id, job_run = job_ids(op_id)
        running = await self._reader.unfinished_jobs(session_id)
        # A re-sent call (a retried step) is the job already running, not one more.
        if job_id not in running and len(running) >= MAX_BACKGROUND_JOBS:
            raise TooManyJobs(
                f"{len(running)} background subagents are running, the most a session may have; "
                "wait for one to end (subagent_result) before starting another"
            )
        job_args: dict[str, Any] = {
            "prompt": args.prompt,
            "description": args.description,
            "mode": "background",
        }
        if args.hand:
            job_args["lease"] = (await self._lend(session_id, args.hand, job_id)).to_dict()
        op = SessionOp(
            session_id=session_id,
            op_id=op_id,
            kind=Op.START_BACKGROUND,
            tenant_id=row.tenant_id,
            args=job_args,
        )
        try:
            provider = await self._manager.resolve_subagent_provider(session_id, None)
            await self._manager.begin_subagent_background(
                session_id,
                tenant_id=row.tenant_id,
                provider=provider,
                request=request,
                job_id=job_id,
                run_id=job_run,
                turn_id=run_id,
            )
            # The session's workflow runs the job durably (a re-sent signal starts it once).
            handle = activity.client().get_workflow_handle(workflow_id(session_id))
            await handle.signal(START_JOB_SIGNAL, StartJob(op=op, provider=provider))
        except BaseException:
            if (lent := Borrower.from_dict(job_args.get("lease"))) is not None:
                # No job will run: the hand goes straight back rather than wait out its lease.
                await self._manager._leases.give_back(
                    hand_key(session_id, lent.hand), lent.holder, lent.token
                )
            raise
        return Outcome(
            {"job_id": job_id, "status": "started"}
            | ({"hand": args.hand, "lent_until": "the job ends"} if args.hand else {})
        )

    async def _lend(self, session_id: str, hand: str, job_id: str) -> Borrower:
        """Lend ``hand`` to the job (a re-sent call renews the same lease)."""
        if hand not in self._manager.hand_names(session_id):
            raise InvalidArguments(
                f"no hand {hand!r}; your hands: {', '.join(self._manager.hand_names(session_id))}"
            )
        lease = await self._manager._leases.lend(hand_key(session_id, hand), job_id)
        if lease is None:
            current = await self._manager._leases.current(hand_key(session_id, hand))
            holder = current.holder if current is not None else "another job"
            raise HandLent(f"your {hand!r} hand is already lent to {holder}")
        return Borrower(hand, lease.holder, lease.token)

    async def unannounced(self, session_id: str) -> list[dict[str, Any]]:
        """Finished background subagents the model has neither been told about nor asked for."""
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

    async def _result(self, session_id: str, args: SubagentResultArgs) -> Outcome:
        started = ended = None
        for envelope in await self._reader.read(session_id):
            event = envelope.payload
            if isinstance(event, JobStart) and event.job_id == args.job_id:
                started = event
            elif isinstance(event, JobEnd) and event.job_id == args.job_id:
                ended = event
        if started is None and ended is None:
            raise InvalidArguments(f"no background subagent with job_id {args.job_id!r}")
        if ended is None:
            return Outcome({"job_id": args.job_id, "status": "running"})
        return Outcome(
            {"job_id": args.job_id, "status": ended.stop_reason, "text": ended.output},
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
    """How finished background subagents are put to the model."""
    lines = ["Background subagents you started have finished:"]
    for result in event.results:
        text = (result.get("text") or "").strip() or "(no output)"
        lines.append(f"- {result['job_id']} ({result['status']}): {text}")
    return "\n".join(lines)
