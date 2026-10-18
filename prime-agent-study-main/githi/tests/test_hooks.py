"""Hooks -- the extension points that keep policy out of the loop.

Pi's equivalent is what lets a harness add behaviour without forking the
agent. The important one here is `before_tool_call`: a hook can *block* a
call before it executes, which is where a destructive-action gate belongs.
Deciding in the harness rather than asking the model to police itself is the
difference between an enforced rule and a suggestion.
"""

from __future__ import annotations

from githi.agent.hooks import BeforeToolCall, Hooks, ToolDecision
from githi.types import ToolCall, UserMessage


class TestBeforeToolCall:
    def test_default_allows(self):
        assert Hooks().before_tool_call(BeforeToolCall(ToolCall("c", "bash"))) is None

    def test_hook_can_block(self):
        hooks = Hooks(before_tool_call=lambda ctx: ToolDecision(block=True, reason="nope"))
        decision = hooks.before_tool_call(BeforeToolCall(ToolCall("c", "bash")))
        assert decision.block is True and decision.reason == "nope"

    def test_hook_can_rewrite_arguments(self):
        """Clamping a model-supplied argument is safer than refusing the call
        and hoping it retries with something sensible."""
        hooks = Hooks(before_tool_call=lambda ctx: ToolDecision(
            arguments={**ctx.call.arguments, "timeout": 5}))
        decision = hooks.before_tool_call(
            BeforeToolCall(ToolCall("c", "bash", {"command": "x", "timeout": 9999})))
        assert decision.arguments["timeout"] == 5

    def test_blocking_needs_no_arguments(self):
        assert ToolDecision(block=True).arguments is None


class TestTransformContext:
    def test_default_is_identity(self):
        msgs = [UserMessage("hi")]
        assert Hooks().transform_context(msgs) == msgs

    def test_hook_can_inject(self):
        """Injecting per-turn context here rather than mutating the stored
        transcript keeps the injection from accumulating turn after turn."""
        hooks = Hooks(transform_context=lambda ms: [UserMessage("briefing"), *ms])
        assert hooks.transform_context([UserMessage("hi")])[0].content == "briefing"

    def test_does_not_mutate_the_original(self):
        original = [UserMessage("hi")]
        Hooks(transform_context=lambda ms: [UserMessage("x"), *ms]).transform_context(original)
        assert original == [UserMessage("hi")]


class TestAfterToolCall:
    def test_default_is_noop(self):
        assert Hooks().after_tool_call(ToolCall("c", "bash"), None) is None

    def test_receives_the_result(self):
        seen = {}
        hooks = Hooks(after_tool_call=lambda call, result: seen.update(
            {"name": call.name, "result": result}))
        hooks.after_tool_call(ToolCall("c", "bash"), "output")
        assert seen == {"name": "bash", "result": "output"}


class TestShouldStop:
    def test_default_does_not_stop(self):
        assert Hooks().should_stop(None) is False

    def test_hook_can_stop_the_run(self):
        assert Hooks(should_stop=lambda state: True).should_stop(None) is True
