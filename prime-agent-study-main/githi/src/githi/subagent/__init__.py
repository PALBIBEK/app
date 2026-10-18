"""In-process one-shot subagent spawn and fork (DeepSeek-style).

Spawn: fresh child transcript.
Fork: seed with parent's completed-turn prefix, then the new prompt.
Background one-shot jobs: `subagent.jobs`.
Continuable sessions: `subagent.continuable` (send_message / settlement / report).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal
from uuid import uuid4

from ..errors import GithiError
from ..types import Message, StopReason
from .seed import completed_turn_prefix

if TYPE_CHECKING:
    from ..agent.agent import Agent
    from ..agent.interrupt import Interrupt

DEFAULT_MAX_DEPTH = 3

# Parent-facing control tools — never copy onto a child Agent.
PARENT_ONLY_TOOLS = frozenset({
    "send_message",
    "interrupt_agent",
    "list_agents",
    "job_output",
    "job_kill",
    "subagent_continuable",
    "ask_user_question",
    "report",  # installed only via ContinuableRegistry._install_report
})

SpawnMode = Literal["spawn", "fork"]


class SubagentError(GithiError):
    """Stable codes for spawn failures the model should see."""

    def __init__(self, message: str, code: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass
class SubagentRunResult:
    """Foreground one-shot outcome after the child has finished."""

    id: str
    output: str
    stop_reason: StopReason
    description: str = ""
    mode: SpawnMode = "spawn"


def delegation_depth_of(extra: dict[str, Any] | None) -> int:
    """Read parent depth from tool context; treat absence as top-level 0."""
    if not extra:
        return 0
    raw = extra.get("delegation_depth", 0)
    try:
        depth = int(raw)
    except (TypeError, ValueError):
        return 0
    return max(0, depth)


def assert_can_spawn(parent_depth: int, max_depth: int) -> None:
    """Reject when the child would exceed the absolute depth cap.

    Child depth is parent + 1. `max_depth` 0 forbids all delegation.
    """
    if not isinstance(max_depth, int) or max_depth < 0:
        raise SubagentError(
            "subagent max_depth must be a non-negative integer",
            "BAD_ARGS",
        )
    child_depth = parent_depth + 1
    if child_depth > max_depth:
        raise SubagentError(
            f"delegation depth cap exceeded: child would be at depth "
            f"{child_depth} but max_depth is {max_depth}",
            "DEPTH_EXCEEDED",
        )


def _with_partial(headline: str, output: str) -> str:
    text = (output or "").strip()
    if not text:
        return headline
    return f"{headline}\nPartial output before the run ended:\n{text}"


def map_run_to_outcome(result: SubagentRunResult) -> tuple[bool, str]:
    """Map child outcome to (success, tool_text) like DeepSeek foreground."""
    output = (result.output or "").strip()
    reason = result.stop_reason

    if reason is StopReason.DONE:
        if not output:
            return False, "subagent finished without producing a final answer"
        return True, output

    if reason is StopReason.INTERRUPTED:
        return False, _with_partial(
            "subagent run was cancelled (interrupted)", output)
    if reason is StopReason.STOPPED:
        return False, _with_partial(
            "subagent run was stopped before finishing", output)
    if reason is StopReason.MAX_TURNS:
        return False, _with_partial(
            "subagent run hit its turn limit before finishing", output)
    if reason is StopReason.ERROR:
        return False, _with_partial("subagent run failed", output)
    return False, _with_partial(
        f"subagent run ended abnormally ({reason.value})", output)


class SubagentService:
    """Spawn or fork one-shot children for a parent Agent.

    Bound to the parent at construction; model-facing tools reach this via
    `ToolContext.extra["spawn_subagent"]` / `["fork_subagent"]`.
    """

    def __init__(
        self,
        parent: Agent,
        *,
        max_depth: int = DEFAULT_MAX_DEPTH,
    ) -> None:
        self._parent = parent
        self.max_depth = max_depth

    def start_one_shot(
        self,
        prompt: str,
        *,
        description: str = "",
        parent_depth: int = 0,
        interrupt: Interrupt | None = None,
        run_id: str | None = None,
    ) -> SubagentRunResult:
        """Fresh child (no parent history)."""
        return self._start(
            prompt,
            description=description,
            parent_depth=parent_depth,
            mode="spawn",
            seed=None,
            interrupt=interrupt,
            run_id=run_id,
        )

    def start_fork(
        self,
        prompt: str,
        *,
        description: str = "",
        parent_depth: int = 0,
        interrupt: Interrupt | None = None,
        run_id: str | None = None,
        seed: list[Message] | None = None,
    ) -> SubagentRunResult:
        """Child seeded with parent's completed-turn prefix.

        Pass `seed=` to reuse a snapshot taken earlier (background jobs must
        capture at ack time, not when the worker eventually runs).
        """
        if seed is None:
            seed = completed_turn_prefix(self._parent.state.messages)
        return self._start(
            prompt,
            description=description,
            parent_depth=parent_depth,
            mode="fork",
            seed=seed or None,
            interrupt=interrupt,
            run_id=run_id,
        )

    def _start(
        self,
        prompt: str,
        *,
        description: str,
        parent_depth: int,
        mode: SpawnMode,
        seed: list[Message] | None,
        interrupt: Interrupt | None = None,
        run_id: str | None = None,
    ) -> SubagentRunResult:
        text = (prompt or "").strip()
        if not text:
            raise SubagentError(
                "subagent requires a non-empty `prompt`",
                "BAD_ARGS",
            )
        assert_can_spawn(parent_depth, self.max_depth)

        child_id = run_id or uuid4().hex[:12]
        child = self._build_child(
            parent_depth=parent_depth,
            seed=seed,
            interrupt=interrupt,
        )
        try:
            result = child.run(text)
            return SubagentRunResult(
                id=child_id,
                output=result.output or "",
                stop_reason=result.stop_reason,
                description=description.strip(),
                mode=mode,
            )
        finally:
            from ..sandbox.active_runs import BashRunRegistry

            child.ask_user = None
            child.bash_runs = BashRunRegistry()

    def _build_child(
        self,
        *,
        parent_depth: int,
        seed: list[Message] | None,
        interrupt: Interrupt | None = None,
    ) -> Agent:
        from ..agent.agent import Agent, AgentConfig
        from ..tools.registry import ToolRegistry

        parent = self._parent
        child_tools = [
            parent.tools.get(name)
            for name in parent.tools.names()
            if name not in PARENT_ONLY_TOOLS
        ]
        tools = ToolRegistry(child_tools)
        config = AgentConfig(
            max_turns=parent.config.max_turns,
            cwd=parent.config.cwd,
            tools_for_prompt=[tools.get(n) for n in tools.names()],
            interrupt=(
                interrupt if interrupt is not None else parent.config.interrupt
            ),
            session_path=None,
            skill_listing=parent.config.skill_listing,
            context_limit_tokens=parent.config.context_limit_tokens,
            warn_turns_remaining=parent.config.warn_turns_remaining,
            force_answer_on_final_turn=parent.config.force_answer_on_final_turn,
            memory=None,
            summarizer=parent.config.summarizer,
        )
        child = Agent(
            llm=parent.llm,
            tools=tools,
            policy=parent.policy,
            config=config,
        )
        child.delegation_depth = parent_depth + 1
        child.delegated = True
        child.ask_user = None
        child.subagents.max_depth = parent.subagents.max_depth
        if seed:
            child.state.messages[:] = seed
        if parent._model is not None:
            child.set_model(parent._model)
        if parent._thinking is not None:
            child.set_thinking_level(parent._thinking)
        return child
