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
from supervisor.seams import CompactionPreparation
from supervisor.session.errors import OperationRetired
from supervisor.session.manager import SessionManager
from supervisor.temporal.contracts import RETIRED_OPS, Op, SessionOp

type Result = dict[str, Any] | None
type Handler = Callable[[SessionManager, SessionOp], Awaitable[Result]]


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


async def _retired(m: SessionManager, op: SessionOp) -> Result:
    raise OperationRetired(
        f"the {op.kind} operation is retired: subagents are started by the agent's own tool "
        "calls (supervisor.session.children); read them with GET /v1/sessions/{id}/subagents"
    )


async def _rehydrate_workspace(m: SessionManager, op: SessionOp) -> Result:
    await m.rehydrate_workspace(op.session_id)
    return None


_HANDLERS: Final[Mapping[Op, Handler]] = {
    Op.INVOKE_TOOL: _invoke_tool,
    Op.COMPACT_MEMORY: _compact_memory,
    Op.RECALL_MEMORY: _recall_memory,
    **dict.fromkeys(RETIRED_OPS, _retired),
    Op.REHYDRATE_WORKSPACE: _rehydrate_workspace,
}
assert _HANDLERS.keys() == set(Op), "every Op needs a handler"


async def run_operation(manager: SessionManager, op: SessionOp) -> Result:
    """Run ``op`` against this process's SessionManager; domain errors propagate."""
    return await _HANDLERS[op.kind](manager, op)
