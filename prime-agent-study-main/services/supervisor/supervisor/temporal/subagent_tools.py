"""Subagent tools: githi delegates through its session's pinned subagent transport.

Offered only when the session's harness pins a ``subagents`` block and its
composition binds a subagent transport (ACP, nested session, …):

* ``subagent`` runs a child on the transport's pinned default provider and
  answers with its result; with ``background`` (when the manifest allows it)
  the child runs as the session's durable ``SubagentJob`` workflow instead.
* ``subagent_result`` reads a background child's outcome from the journal.

A background child that finishes is also announced without being asked: the
next run starts with a ``BackgroundResults`` event listing every finished job
the model has not yet seen, shown ahead of that run's user message.

Depth, background permission and provider all come from what the session
pinned; nothing here is chosen by the model beyond the task itself.
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
    "SUBAGENT",
    "SUBAGENT_RESULT",
    "SUBAGENT_TOOLS",
    "SubagentTools",
    "announcement",
    "render",
]

SUBAGENT: Final = "subagent"
SUBAGENT_RESULT: Final = "subagent_result"
SUBAGENT_TOOLS: Final = frozenset({SUBAGENT, SUBAGENT_RESULT})


class SubagentArgs(ToolArgs):
    prompt: str
    """The task for the subagent, with everything it needs: it does not see this conversation"""

    description: str = ""
    """A few words naming the task"""

    background: bool = False
    """Start it and return at once with a job_id; read its outcome later with subagent_result"""

    hand: str = ""
    """Background only: lend it one of your sandboxes to work in; you get it back when it ends"""


class HandLent(ToolError):
    code = "HAND_LENT"


class SubagentResultArgs(ToolArgs):
    job_id: str
    """The job_id a background subagent call returned"""


@dataclass(frozen=True, slots=True)
class Outcome:
    payload: Any
    is_error: bool = False


class SubagentTools:
    def __init__(self, manager: SessionManager, reader: JournalReader) -> None:
        self._manager = manager
        self._reader = reader

    async def schemas(self, session_id: str) -> list[dict[str, Any]]:
        """The subagent tools this session may use (none unless it pinned subagents)."""
        row = await self._manager._get_session_row(session_id)
        spec = self._manager._pinned_manifest(row).subagents
        if spec is None or spec.max_depth <= 0 or not self._has_transport(session_id, row):
            return []
        task = json_schema(SubagentArgs)
        if not spec.allow_background:
            del task["properties"]["background"]
        hands = self._manager.hand_names(session_id)
        if not spec.allow_background or len(hands) <= 1:
            del task["properties"]["hand"]
        else:
            task["properties"]["hand"]["enum"] = hands
        tools = [
            _function(
                SUBAGENT, "Delegate a self-contained task to a subagent and get its answer.", task
            )
        ]
        if spec.allow_background:
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
        row = await self._manager._get_session_row(session_id)
        spec = self._manager._pinned_manifest(row).subagents
        if args.background and not (spec is not None and spec.allow_background):
            raise InvalidArguments("this agent may not start background subagents")
        request = SubagentRequest(
            prompt=args.prompt,
            description=args.description,
            mode="background" if args.background else "spawn",
        )
        if args.hand and not args.background:
            raise InvalidArguments("only a background subagent can borrow a hand")
        if not args.background:
            result = await self._manager.spawn_child(
                session_id, tenant_id=row.tenant_id, provider=None, request=request, turn_id=run_id
            )
            return Outcome(
                {"child_session_id": result.child_id, "status": result.status, "text": result.text},
                is_error=result.status not in ("ok", "stop", None),
            )

        op_id = f"{run_id}:{call_id}"
        job_id, job_run = job_ids(op_id)
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
