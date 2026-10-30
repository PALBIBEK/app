"""A background subagent that works in a hand its session lent it.

The job is a real agent: the session's model (through its gateway key) and the
session's sandbox tools, in a loop, until it answers. Its brain runs on whichever
worker runs the job; every tool call goes through the *parent* session's tool
operation, carrying the lease, so it runs where the hand lives, is journalled in
the session like any call, and is refused once the lease has ended (fencing).

The session's journal is the job's record. A job retried on another worker (its
worker died, or a deploy stopped it) resumes from it rather than start over: the
calls it made are read back, asked again by the same id (each runs once, so a
retry gets its result, and one still running is waited for) and shown to the model,
which goes on from there. Each call's id names its step, its place and what it
asks, so a call is never answered with another's result.

The hand comes back when the job ends, however it ends (``JobSteps.end``).
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final

from temporalio import activity

from supervisor.agent.events import ToolStart
from supervisor.agent.runtimes.githi.loop import AgentLoop
from supervisor.policy.guards.detectors import call_hash
from supervisor.seams import ChatMessage, SubagentRequest
from supervisor.seams.interfaces.tool_provider import needs_workspace
from supervisor.temporal.contracts import Op, SessionOp

if TYPE_CHECKING:
    from supervisor.hands.leases import Borrower
    from supervisor.journal.reader import JournalReader
    from supervisor.session import SessionManager
    from supervisor.temporal.contracts import JobSpec

__all__ = ["MAX_STEPS", "HandJobResult", "JobCall", "calls_made", "run_hand_job"]

MAX_STEPS: Final = 20
"""Model calls a hand job may make before it stops without an answer."""

_BRIEF: Final = (
    "You are a background subagent. You work alone in the sandbox your session lent you "
    "(its {hand!r} hand) with the tools given: use them to do the task, then answer with "
    "what you did and found. Your answer is all your session sees."
)

type Invoke = Callable[[SessionOp], Awaitable[dict[str, Any] | None]]


@dataclass(frozen=True, slots=True)
class HandJobResult:
    stop_reason: str  # ok | error | interrupted
    text: str


@dataclass(frozen=True, slots=True)
class JobCall:
    """One tool call of a job; ``op_id`` is its session operation's id and its call id."""

    op_id: str
    name: str
    arguments: dict[str, Any]

    @classmethod
    def asked(cls, job_id: str, step: int, place: int, name: str, arguments: dict[str, Any]) -> JobCall:
        return cls(f"{job_id}:{step}:{place}:{call_hash(name, arguments)}", name, arguments)


def calls_made(job_id: str, starts: Sequence[ToolStart]) -> list[list[JobCall]]:
    """The calls the job made, step by step, from the session's journalled tool starts.

    A step's calls start one after another, so a step cut off part way holds those
    that started (the model goes on from what they did); a step whose reply was never
    acted on left nothing, and is asked again.
    """
    steps: dict[int, list[tuple[int, JobCall]]] = {}
    for start in starts:
        job, _, rest = start.tool_call_id.partition(":")
        # ``step:place:what``; ``step:place`` before ids named what a call asks.
        parts = rest.split(":")
        if job != job_id or len(parts) not in (2, 3) or not (parts[0].isdigit() and parts[1].isdigit()):
            continue
        step, place = int(parts[0]), int(parts[1])
        call = JobCall(start.tool_call_id, start.tool_name, dict(start.args))
        steps.setdefault(step, []).append((place, call))
    made: list[list[JobCall]] = []
    for step in range(len(steps)):  # steps run in order: stop at the first missing one
        if step not in steps:
            break
        made.append([call for _, call in sorted(steps[step], key=lambda placed: placed[0])])
    return made


async def run_hand_job(
    manager: SessionManager,
    spec: JobSpec,
    request: SubagentRequest,
    lease: Borrower,
    *,
    reader: JournalReader | None = None,
    invoke: Invoke | None = None,
) -> HandJobResult:
    """Run the job's agent loop in the borrowed hand.

    ``reader``: the session's journal, read when this is a retried attempt, to resume.
    ``invoke`` overrides the tool route (tests).
    """
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

    async def run(call: JobCall) -> dict[str, Any]:
        """The call's result: run once in the session, whoever asks and however often."""
        args = {"name": call.name, "arguments": call.arguments, "lease": lease.to_dict()}
        body = await invoke(SessionOp(spec.session_id, call.op_id, Op.INVOKE_TOOL, row.tenant_id, args))
        return body or {}

    messages = [
        ChatMessage(role="system", content=_BRIEF.format(hand=lease.hand)),
        ChatMessage(role="user", content=request.prompt),
    ]
    made = await _made_before(reader, spec) if reader is not None else []
    for step in range(MAX_STEPS):
        if step < len(made):  # resumed: what an earlier attempt asked for
            text, calls = "", made[step]
        else:
            # Its model calls are the session's to pay for: none past the session's cost cap.
            if (spent := await manager.cost_cap_reached(spec.session_id)) is not None:
                return HandJobResult("error", f"the session may spend no more ({spent})")
            completion = await heartbeating(
                asyncio.to_thread(seams.model_gateway.complete, messages, schemas, model=brain.model)
            )
            await manager.charge_cost(spec.session_id, completion.spent["cost"])
            text = str(completion.message.content or "")
            asked = AgentLoop.extract_tool_calls(completion)
            if not asked:
                return HandJobResult("ok", text)
            calls = [
                JobCall.asked(spec.job_id, step, place, str(c["name"]), dict(c["arguments"]))
                for place, c in enumerate(asked)
            ]
        messages.append(ChatMessage(role="assistant", content=text, tool_calls=[_shown(c) for c in calls]))
        for call in calls:
            body = await run(call)
            if body.get("code") == "LEASE_ENDED":
                return HandJobResult("interrupted", "the hand was taken back before the job finished")
            messages.append(ChatMessage(role="tool", content=json.dumps(body, default=str), tool_call_id=call.op_id))
    return HandJobResult("error", f"no answer after {MAX_STEPS} model calls")


async def _made_before(reader: JournalReader, spec: JobSpec) -> list[list[JobCall]]:
    starts = await reader.read_types([spec.session_id], [ToolStart.__name__])
    return calls_made(spec.job_id, [e.payload for e in starts if isinstance(e.payload, ToolStart)])


def _shown(call: JobCall) -> dict[str, Any]:
    """The call as the model reads it back (its id is the operation's, so a resumed
    conversation reads as the original did)."""
    return {
        "id": call.op_id,
        "type": "function",
        "function": {"name": call.name, "arguments": json.dumps(call.arguments)},
    }
