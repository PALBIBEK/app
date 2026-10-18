"""Extension points, so policy lives outside the loop.

`before_tool_call` is the important one: it can block or rewrite a call
*before* it executes. That is where a destructive-action gate belongs --
decided by the harness, not by asking the model to police itself. A rule the
model is merely told about is a suggestion; a rule enforced here is a rule.

Ported from githi/src/githi/agent/hooks.py for use in the githi runtime.
Uses RuntimeSeams for dependency injection.

A plain class rather than a dataclass, because each hook is both a
constructor argument and a method of the same name: callers pass raw
callables, the loop calls uniform methods, and neither side has to
null-check.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, TYPE_CHECKING

if TYPE_CHECKING:
    from supervisor.agent.runtimes.githi.state import GithiAgentState


@dataclass
class ToolCall:
    """A tool call from the model."""
    id: str
    name: str
    arguments: dict[str, Any]
    malformed: bool = False


@dataclass
class BeforeToolCall:
    """Context for before_tool_call hook."""
    call: ToolCall
    state: Any = None


@dataclass
class ToolDecision:
    """Result of before_tool_call hook."""
    block: bool = False
    reason: str = ""
    arguments: dict[str, Any] | None = None
    """Rewritten arguments, or None to leave them unchanged. Clamping a
    model-supplied value is often better than refusing outright and hoping
    the retry is more sensible."""


@dataclass
class PrepareNextTurnContext:
    """Context for prepare_next_turn hook."""
    message: Any
    tool_results: list[Any]
    context: Any
    new_messages: list[Any]
    state: Any


@dataclass
class PrepareNextTurnResult:
    """Result of prepare_next_turn hook."""
    context: Any = None
    model: str | None = None
    thinking_level: Any = None


class Hooks:
    """Hook collection for agent runtime customization.

    Hooks allow external control over:
    - Tool call gating (before_tool_call)
    - Tool call observation (after_tool_call)
    - Context transformation (transform_context)
    - Run termination (should_stop)
    - Turn preparation (prepare_next_turn)
    - Steering messages (get_steering_messages)
    - Follow-up messages (get_follow_up_messages)
    """

    def __init__(
        self,
        before_tool_call: Callable[[BeforeToolCall], ToolDecision | None] | None = None,
        after_tool_call: Callable[[ToolCall, Any], None] | None = None,
        transform_context: Callable[[list], list] | None = None,
        should_stop: Callable[[Any], bool] | None = None,
        prepare_next_turn: Callable[[PrepareNextTurnContext], PrepareNextTurnResult | None] | None = None,
        get_steering_messages: Callable[[], list] | None = None,
        get_follow_up_messages: Callable[[], list] | None = None,
    ) -> None:
        self._before = before_tool_call
        self._after = after_tool_call
        self._transform = transform_context
        self._stop = should_stop
        self._prepare = prepare_next_turn
        self._steering = get_steering_messages
        self._follow_up = get_follow_up_messages

    def before_tool_call(self, ctx: BeforeToolCall) -> ToolDecision | None:
        """Evaluate a tool call before execution.

        Returns:
            ToolDecision to block/rewrite, or None to allow unchanged.
        """
        return self._before(ctx) if self._before else None

    def after_tool_call(self, call: ToolCall, result: Any) -> None:
        """Observe a tool call result after execution."""
        if self._after:
            self._after(call, result)
        return None

    def transform_context(self, messages: list) -> list:
        """Transform the message context before sending to LLM.

        Always handed a copy, so a hook that prepends cannot mutate the
        caller's stored transcript -- which would make the injection
        accumulate on every subsequent turn.
        """
        return self._transform(list(messages)) if self._transform else messages

    def should_stop(self, state: Any) -> bool:
        """Check if the run should stop early."""
        return bool(self._stop(state)) if self._stop else False

    def prepare_next_turn(self, ctx: PrepareNextTurnContext) -> PrepareNextTurnResult | None:
        """Prepare for the next turn.

        Returns replacement runtime state for the next request, or None to keep
        what the loop already had. This is what a phase machine is built on:
        returning a new context swaps the prompt and tool set between turns.
        """
        return self._prepare(ctx) if self._prepare else None

    def get_steering_messages(self) -> list:
        """Get messages the user queued while the agent was working.

        Drained before the next assistant response, not after the run --
        steering that arrives after the work is finished is not steering.
        """
        return list(self._steering() or []) if self._steering else []

    def get_follow_up_messages(self) -> list:
        """Get messages queued for after the agent would otherwise have stopped.

        Distinct from steering: these restart a finished run rather than
        redirecting one in progress.
        """
        return list(self._follow_up() or []) if self._follow_up else []


# Default empty hooks instance
EMPTY_HOOKS = Hooks()
