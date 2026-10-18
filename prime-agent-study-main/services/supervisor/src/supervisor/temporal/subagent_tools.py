"""Subagent tools: githi delegates through its session's pinned subagent transport.

Offered only when the session's harness pins a ``subagents`` block and its
composition binds a subagent transport (ACP, nested session, …):

* ``subagent`` runs a child on the transport's pinned default provider and
  answers with its result; with ``background`` (when the manifest allows it)
  the child runs as the session's durable ``SubagentJob`` workflow instead.
* ``subagent_result`` reads a background child's outcome from the journal.

Depth, background permission and provider all come from what the session
pinned; nothing here is chosen by the model beyond the task itself.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final

from pydantic import ValidationError
from temporalio import activity

from supervisor.agent.events import JobEnd, JobStart
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

__all__ = ["SUBAGENT", "SUBAGENT_RESULT", "SUBAGENT_TOOLS", "SubagentTools"]

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
        if not args.background:
            result = await self._manager.spawn_child(
                session_id, tenant_id=row.tenant_id, provider=None, request=request, turn_id=run_id
            )
            return Outcome(
                {"child_session_id": result.child_id, "status": result.status, "text": result.text},
                is_error=result.status not in ("ok", "stop", None),
            )

        op = SessionOp(
            session_id=session_id,
            op_id=f"{run_id}:{call_id}",
            kind=Op.START_BACKGROUND,
            tenant_id=row.tenant_id,
            args={"prompt": args.prompt, "description": args.description, "mode": "background"},
        )
        job_id, job_run = job_ids(op.op_id)
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
        return Outcome({"job_id": job_id, "status": "started"})

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
