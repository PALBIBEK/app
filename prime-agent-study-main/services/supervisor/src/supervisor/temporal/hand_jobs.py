"""A background subagent that works in a hand its session lent it.

The job is a real agent: the session's model (through its gateway key) and the
session's sandbox tools, in a loop, until it answers. Its brain runs on whichever
worker runs the job; every tool call goes through the *parent* session's tool
operation, carrying the lease, so it runs where the hand lives, is journalled in
the session like any call, and is refused once the lease has ended (fencing).

The hand comes back when the job ends, however it ends (``JobSteps.end``).
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final

from temporalio import activity

from supervisor.agent.runtimes.githi.loop import AgentLoop
from supervisor.seams import ChatMessage, SubagentRequest
from supervisor.seams.interfaces.tool_provider import needs_workspace
from supervisor.temporal.contracts import Op, SessionOp

if TYPE_CHECKING:
    from supervisor.hands.leases import Borrower
    from supervisor.session import SessionManager
    from supervisor.temporal.contracts import JobSpec

__all__ = ["MAX_STEPS", "HandJobResult", "run_hand_job"]

MAX_STEPS: Final = 20
"""Model calls a hand job may make before it stops without an answer."""

_BRIEF: Final = (
    "You are a background subagent. You work alone in the sandbox your session lent you "
    "(its {hand!r} hand) with the tools given: use them to do the task, then answer with "
    "what you did and found. Your answer is all your session sees."
)


@dataclass(frozen=True, slots=True)
class HandJobResult:
    stop_reason: str  # ok | error | interrupted
    text: str


async def run_hand_job(
    manager: SessionManager,
    spec: JobSpec,
    request: SubagentRequest,
    lease: Borrower,
    *,
    invoke: Any | None = None,
) -> HandJobResult:
    """Run the job's agent loop in the borrowed hand; ``invoke`` overrides the tool route (tests)."""
    from supervisor.temporal.sessions import SessionWorkflows
    from supervisor.temporal.steps import heartbeating

    brain = await manager.brain(spec.session_id)
    await manager.refresh_credentials(spec.session_id)
    seams = brain.seams
    assert seams is not None and seams.model_gateway is not None, "a hand job needs the session's model"
    loop = brain.loop()
    schemas = [s for s in loop.tool_schemas() if needs_workspace(loop.tool_provider, s["function"]["name"])]
    row = await manager._get_session_row(spec.session_id)
    if invoke is None:
        sessions = SessionWorkflows(activity.client(), task_queue=activity.info().task_queue)
        invoke = sessions.op

    messages = [
        ChatMessage(role="system", content=_BRIEF.format(hand=lease.hand)),
        ChatMessage(role="user", content=request.prompt),
    ]
    for step in range(MAX_STEPS):
        completion = await heartbeating(
            asyncio.to_thread(seams.model_gateway.complete, messages, schemas, model=brain.model)
        )
        calls = AgentLoop.extract_tool_calls(completion)
        if not calls:
            return HandJobResult("ok", str(completion.message.content or ""))
        messages.append(completion.message)
        for i, call in enumerate(calls):
            body = await invoke(
                SessionOp(
                    spec.session_id,
                    f"{spec.job_id}:{step}:{i}",  # a retried step runs each call once
                    Op.INVOKE_TOOL,
                    row.tenant_id,
                    {"name": call["name"], "arguments": call["arguments"], "lease": lease.to_dict()},
                )
            )
            body = body or {}
            if body.get("code") == "LEASE_ENDED":
                return HandJobResult("interrupted", "the hand was taken back before the job finished")
            messages.append(
                ChatMessage(role="tool", content=json.dumps(body, default=str), tool_call_id=call["id"])
            )
    return HandJobResult("error", f"no answer after {MAX_STEPS} model calls")
