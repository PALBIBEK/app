"""Session operations besides turns, as one dispatch table over SessionManager.

The session's worker runs these inside the ``session.op`` activity; in-process
mode (tests) calls the same table directly. Results are the JSON bodies the
HTTP API returns, so every path shares one conversion.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import asdict
from typing import Any, Final

from supervisor.hands.leases import Borrower
from supervisor.seams import CompactionPreparation, SubagentRequest
from supervisor.session.manager import SessionManager
from supervisor.temporal.contracts import Op, SessionOp, job_ids

type Result = dict[str, Any] | None
type Handler = Callable[[SessionManager, SessionOp], Awaitable[Result]]


def _subagent(op: SessionOp) -> dict[str, Any]:
    args = dict(op.args)
    provider = args.pop("provider", None)  # None: the session's pinned default
    return {
        "tenant_id": op.tenant_id,
        "provider": provider,
        "request": SubagentRequest(**args),
    }


async def _invoke_tool(m: SessionManager, op: SessionOp) -> Result:
    outcome = await m.invoke_tool(
        op.session_id,
        tenant_id=op.tenant_id,
        name=op.args["name"],
        arguments=op.args.get("arguments") or {},
        tool_call_id=op.op_id,
        borrower=Borrower.from_dict(op.args.get("lease")),
    )
    result = outcome.result
    return {
        "tool_call_id": outcome.tool_call_id,
        "ok": result.ok,
        "payload": result.payload,
        "error": result.error,
        "code": result.code,
    }


async def _compact_memory(m: SessionManager, op: SessionOp) -> Result:
    args = dict(op.args)
    reason = args.pop("reason", "manual")
    outcome = await m.compact_memory(
        op.session_id,
        tenant_id=op.tenant_id,
        preparation=CompactionPreparation(**args),
        reason=reason,
    )
    return asdict(outcome)


async def _recall_memory(m: SessionManager, op: SessionOp) -> Result:
    outcome = await m.recall_memory(
        op.session_id, tenant_id=op.tenant_id, user_prompt=op.args["user_prompt"]
    )
    return asdict(outcome)


def _run_result(result: Any) -> Result:
    return {
        "child_session_id": result.child_id or "",
        "text": result.text or None,
        "status": result.status,
    }


async def _spawn_child(m: SessionManager, op: SessionOp) -> Result:
    return _run_result(await m.spawn_child(op.session_id, **_subagent(op)))


async def _fork_child(m: SessionManager, op: SessionOp) -> Result:
    return _run_result(await m.fork_child(op.session_id, **_subagent(op)))


async def _start_background(m: SessionManager, op: SessionOp) -> Result:
    """Validate and journal the job; the session's workflow then runs it durably.

    The provider is resolved here (the workflow cannot see the session's seams)
    and returned, so the job runs on exactly the provider that was validated.
    """
    job_id, run_id = job_ids(op.op_id)
    args = _subagent(op)
    args["provider"] = await m.resolve_subagent_provider(op.session_id, args["provider"])
    handle = await m.begin_subagent_background(op.session_id, **args, job_id=job_id, run_id=run_id)
    return {"job_id": handle.job_id, "provider": args["provider"]}


async def run_job_inline(m: SessionManager, op: SessionOp) -> None:
    """In-process mode only (no workflows): run a started job to its end, here and now."""
    job_id, run_id = job_ids(op.op_id)
    args = _subagent(op)
    request = args["request"]
    provider = await m.resolve_subagent_provider(op.session_id, args["provider"])
    ops = await m.subagent_job_ops(op.session_id, request)
    try:
        result = await ops.run_background(provider, request)
        stop_reason, output = result.status or "ok", result.text or None
    except Exception as exc:  # noqa: BLE001 - any provider failure is the job's end
        stop_reason, output = "error", str(exc)
    await ops.finish_background(
        job_id=job_id, run_id=run_id, stop_reason=stop_reason, output=output
    )


async def _start_continuable(m: SessionManager, op: SessionOp) -> Result:
    handle = await m.start_subagent_continuable(op.session_id, **_subagent(op))
    return {"child_id": handle.child_id, "message_id": handle.message_id or None}


async def _send_child_message(m: SessionManager, op: SessionOp) -> Result:
    await m.send_child_message(
        op.session_id,
        tenant_id=op.tenant_id,
        child_id=op.args["child_id"],
        text=op.args["text"],
    )
    return None


async def _interrupt_child(m: SessionManager, op: SessionOp) -> Result:
    await m.interrupt_child(op.session_id, tenant_id=op.tenant_id, child_id=op.args["child_id"])
    return None


async def _rehydrate_workspace(m: SessionManager, op: SessionOp) -> Result:
    await m.rehydrate_workspace(op.session_id)
    return None


_HANDLERS: Final[Mapping[Op, Handler]] = {
    Op.INVOKE_TOOL: _invoke_tool,
    Op.COMPACT_MEMORY: _compact_memory,
    Op.RECALL_MEMORY: _recall_memory,
    Op.SPAWN_CHILD: _spawn_child,
    Op.FORK_CHILD: _fork_child,
    Op.START_BACKGROUND: _start_background,
    Op.START_CONTINUABLE: _start_continuable,
    Op.SEND_CHILD_MESSAGE: _send_child_message,
    Op.INTERRUPT_CHILD: _interrupt_child,
    Op.REHYDRATE_WORKSPACE: _rehydrate_workspace,
}
assert _HANDLERS.keys() == set(Op), "every Op needs a handler"


async def run_operation(manager: SessionManager, op: SessionOp) -> Result:
    """Run ``op`` against this process's SessionManager; domain errors propagate."""
    return await _HANDLERS[op.kind](manager, op)
