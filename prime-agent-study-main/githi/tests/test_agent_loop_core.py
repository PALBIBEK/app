"""pi's `agent-loop.ts` control flow that githi's first loop omitted.

The original loop was: ask, run tools, repeat. pi's has structure that only
matters when something goes wrong or the user intervenes:

* **Two nested loops.** The inner one drains tool calls *and* steering
  messages; the outer one restarts when follow-up messages arrive after the
  agent would otherwise have stopped.
* **`stop_reason == "length"` fails the whole batch.** A response cut off by
  the token limit may have truncated arguments on *every* tool call in it, so
  executing any of them risks acting on half a command.
* **`terminate` needs unanimity.** One tool must not end a run the others in
  the same batch are still working on.
* **`prepare_next_turn` swaps runtime state between turns** — the primitive a
  phase machine is built from.
"""

from __future__ import annotations

import pytest

from githi.agent.hooks import Hooks
from githi.agent.loop import LoopConfig, run_loop
from githi.agent.state import AgentState
from githi.llm.client import Completion
from githi.sandbox.policy import SandboxPolicy
from githi.tools.base import Tool, ToolContext, ToolResult
from githi.tools.registry import ToolRegistry
from githi.types import (
    AgentContext,
    AgentLoopTurnUpdate,
    AssistantMessage,
    ThinkingLevel,
    ToolCall,
    ToolResultMessage,
    Usage,
    UserMessage,
)


class ScriptedLLM:
    def __init__(self, script, stop_reasons=None):
        self.script = list(script)
        self.stop_reasons = list(stop_reasons or [])
        self.calls: list[AgentContext] = []
        self.model = "test-model"

    def complete(self, messages, tools, **kw):
        self.calls.append(list(messages))
        message = self.script.pop(0) if self.script else AssistantMessage(content="done")
        reason = self.stop_reasons.pop(0) if self.stop_reasons else "stop"
        return Completion(message=message, usage=Usage(input_tokens=1),
                          finish_reason=reason)


def _tool(name="bash", recorder=None, **result_kw):
    def run(args, ctx):
        if recorder is not None:
            recorder.append(args)
        return ToolResult(content=f"ran {name}", **result_kw)
    return Tool(name=name, description="d", parameters={"type": "object"}, run=run)


def _call(i, name="bash"):
    return ToolCall(id=f"c{i}", name=name, arguments={})


def _run(script, tools=None, hooks=None, tmp_path=None, stop_reasons=None, **cfg):
    llm = ScriptedLLM(script, stop_reasons)
    state = AgentState()
    context = AgentContext(system_prompt="sys", messages=[UserMessage("go")])
    run_loop(
        llm=llm,
        context=context,
        tools=ToolRegistry(tools or [_tool()]),
        tool_ctx=ToolContext(policy=SandboxPolicy(root=tmp_path)),
        state=state,
        hooks=hooks or Hooks(),
        config=LoopConfig(**cfg),
    )
    return llm, state


class TestBasicFlow:
    def test_plain_answer_ends_the_run(self, tmp_path):
        _, state = _run([AssistantMessage(content="answer")], tmp_path=tmp_path)
        assert state.turns == 1

    def test_tool_call_then_answer(self, tmp_path):
        recorder = []
        script = [AssistantMessage(content="", tool_calls=[_call(1)]),
                  AssistantMessage(content="done")]
        _run(script, tools=[_tool(recorder=recorder)], tmp_path=tmp_path)
        assert len(recorder) == 1

    def test_every_call_gets_a_result(self, tmp_path):
        script = [AssistantMessage(content="", tool_calls=[_call(1), _call(2)]),
                  AssistantMessage(content="done")]
        llm, _ = _run(script, tmp_path=tmp_path)
        ids = {getattr(m, "tool_call_id", None) for m in llm.calls[1]}
        assert {"c1", "c2"} <= ids


class TestTruncatedResponse:
    def test_length_stop_fails_every_call_in_the_batch(self, tmp_path):
        """A response cut off mid-JSON may have truncated arguments on any of
        its calls. Executing them risks running half a command."""
        recorder = []
        script = [AssistantMessage(content="", tool_calls=[_call(1), _call(2)]),
                  AssistantMessage(content="recovered")]
        llm, _ = _run(script, tools=[_tool(recorder=recorder)], tmp_path=tmp_path,
                      stop_reasons=["length", "stop"])
        assert recorder == [], "executed tool calls from a truncated message"

    def test_failed_calls_still_produce_results(self, tmp_path):
        """Otherwise the transcript has orphaned tool_calls and the next
        request is rejected by the provider."""
        script = [AssistantMessage(content="", tool_calls=[_call(1), _call(2)]),
                  AssistantMessage(content="recovered")]
        llm, _ = _run(script, tmp_path=tmp_path, stop_reasons=["length", "stop"])
        results = [m for m in llm.calls[1] if isinstance(m, ToolResultMessage)]
        assert len(results) == 2 and all(r.is_error for r in results)

    def test_error_stop_reason_ends_the_run(self, tmp_path):
        _, state = _run([AssistantMessage(content="")], tmp_path=tmp_path,
                        stop_reasons=["error"])
        assert state.turns == 1


class TestTerminate:
    def test_unanimous_terminate_stops_the_loop(self, tmp_path):
        script = [AssistantMessage(content="", tool_calls=[_call(1), _call(2)]),
                  AssistantMessage(content="should not be reached")]
        llm, _ = _run(script, tools=[_tool(terminate=True)], tmp_path=tmp_path)
        assert len(llm.calls) == 1, "loop continued after a unanimous terminate"

    def test_one_terminate_among_several_does_not_stop(self, tmp_path):
        """pi requires *every* result in the batch to set it -- one tool must
        not end a run the others are still working on."""
        script = [AssistantMessage(content="",
                                   tool_calls=[_call(1, "quitter"), _call(2, "worker")]),
                  AssistantMessage(content="continued")]
        llm, _ = _run(script,
                      tools=[_tool("quitter", terminate=True), _tool("worker")],
                      tmp_path=tmp_path)
        assert len(llm.calls) == 2

    def test_empty_batch_does_not_terminate(self, tmp_path):
        """`every()` over an empty list is vacuously true -- pi guards with an
        explicit length check, otherwise a no-tool turn would end the run."""
        from githi.agent.loop import should_terminate_batch

        assert should_terminate_batch([]) is False


class TestPrepareNextTurn:
    def test_can_replace_the_context(self, tmp_path):
        """The phase-machine primitive: swap the system prompt between turns
        with no separate framework."""
        script = [AssistantMessage(content="", tool_calls=[_call(1)]),
                  AssistantMessage(content="done")]
        hooks = Hooks(prepare_next_turn=lambda ctx: AgentLoopTurnUpdate(
            context=AgentContext(system_prompt="PHASE TWO",
                                 messages=ctx.context.messages)))
        llm, _ = _run(script, hooks=hooks, tmp_path=tmp_path)
        assert any(getattr(m, "content", "") == "PHASE TWO" for m in llm.calls[1])

    def test_can_replace_model_and_thinking(self, tmp_path):
        seen = {}
        script = [AssistantMessage(content="", tool_calls=[_call(1)]),
                  AssistantMessage(content="done")]

        class Recording(ScriptedLLM):
            def complete(self, messages, tools, **kw):
                seen.update(kw)
                return super().complete(messages, tools, **kw)

        llm = Recording(script)
        run_loop(llm=llm, context=AgentContext(system_prompt="s", messages=[]),
                 tools=ToolRegistry([_tool()]),
                 tool_ctx=ToolContext(policy=SandboxPolicy(root=tmp_path)),
                 state=AgentState(),
                 hooks=Hooks(prepare_next_turn=lambda ctx: AgentLoopTurnUpdate(
                     model="better-model", thinking_level=ThinkingLevel.HIGH)),
                 config=LoopConfig())
        assert seen.get("model") == "better-model"
        assert seen.get("thinking_level") is ThinkingLevel.HIGH

    def test_returning_none_keeps_current_state(self, tmp_path):
        script = [AssistantMessage(content="", tool_calls=[_call(1)]),
                  AssistantMessage(content="done")]
        llm, _ = _run(script, hooks=Hooks(prepare_next_turn=lambda ctx: None),
                      tmp_path=tmp_path)
        assert len(llm.calls) == 2


class TestSteeringAndFollowUp:
    def test_steering_message_is_injected_before_the_next_response(self, tmp_path):
        """The user typed while the agent was working; pi drains the queue
        before the next assistant turn rather than after the run."""
        queue = [[UserMessage("actually, do X instead")], []]
        script = [AssistantMessage(content="", tool_calls=[_call(1)]),
                  AssistantMessage(content="done")]
        llm, _ = _run(script, hooks=Hooks(get_steering_messages=lambda: queue.pop(0)),
                      tmp_path=tmp_path)
        assert any(getattr(m, "content", "") == "actually, do X instead"
                   for m in llm.calls[-1])

    def test_follow_up_restarts_a_finished_run(self, tmp_path):
        """The outer loop: the agent would have stopped, but a queued
        follow-up arrived, so it continues instead of exiting."""
        followups = [[UserMessage("one more thing")], []]
        script = [AssistantMessage(content="first answer"),
                  AssistantMessage(content="second answer")]
        llm, _ = _run(script, hooks=Hooks(get_follow_up_messages=lambda: followups.pop(0)),
                      tmp_path=tmp_path)
        assert len(llm.calls) == 2

    def test_no_queues_means_a_single_pass(self, tmp_path):
        llm, _ = _run([AssistantMessage(content="done")], tmp_path=tmp_path)
        assert len(llm.calls) == 1


class TestLimits:
    def test_max_turns_is_enforced(self, tmp_path):
        script = [AssistantMessage(content="", tool_calls=[_call(i)]) for i in range(20)]
        llm, state = _run(script, tmp_path=tmp_path, max_turns=3)
        assert state.turns == 3

    def test_should_stop_hook_ends_the_run(self, tmp_path):
        script = [AssistantMessage(content="", tool_calls=[_call(i)]) for i in range(10)]
        llm, state = _run(script, hooks=Hooks(should_stop=lambda s: s.turns >= 2),
                          tmp_path=tmp_path)
        assert state.turns == 2


class TestAddedTools:
    def test_a_tool_can_introduce_another_tool(self, tmp_path):
        """`added_tool_names` -- how a synthesised capability becomes callable
        from the next turn onward."""
        registry = ToolRegistry([Tool(
            name="builder", description="d", parameters={"type": "object"},
            run=lambda a, c: ToolResult(content="built",
                                        added_tool_names=["generated"]))])
        registry.add(_tool("generated"))

        script = [AssistantMessage(content="", tool_calls=[_call(1, "builder")]),
                  AssistantMessage(content="", tool_calls=[_call(2, "generated")]),
                  AssistantMessage(content="done")]
        llm = ScriptedLLM(script)
        state = AgentState()
        run_loop(llm=llm, context=AgentContext(system_prompt="s", messages=[]),
                 tools=registry,
                 tool_ctx=ToolContext(policy=SandboxPolicy(root=tmp_path)),
                 state=state, hooks=Hooks(), config=LoopConfig())
        assert state.tool_counts["generated"] == 1
