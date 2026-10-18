"""The agent loop.

Driven by a scripted fake LLM, so the whole control flow is testable with no
network, no key and no cost. Every branch that matters -- tool batching,
budget exhaustion, blocked calls, crashing tools, compaction -- is exercised
offline.
"""

from __future__ import annotations

import pytest

from githi.agent.agent import Agent, AgentConfig
from githi.agent.hooks import Hooks, ToolDecision
from githi.llm.client import Completion
from githi.sandbox.policy import SandboxPolicy
from githi.tools.base import Tool, ToolResult
from githi.tools.registry import ToolRegistry
from githi.types import AssistantMessage, StopReason, ToolCall, Usage


class FakeLLM:
    """Returns scripted completions and records what it was sent."""

    def __init__(self, script: list[AssistantMessage]) -> None:
        self.script = list(script)
        self.calls: list[list] = []
        self.tools_seen: list[list] = []
        self.model = "anthropic/claude-haiku-4.5"

    def complete(self, messages, tools):
        self.calls.append(list(messages))
        self.tools_seen.append(list(tools))
        message = self.script.pop(0) if self.script else AssistantMessage(content="done")
        return Completion(message=message, usage=Usage(input_tokens=10, output_tokens=2))


def _echo_tool(recorder: list) -> Tool:
    def run(args, ctx):
        recorder.append(args)
        return ToolResult(content=f"ran {args.get('command', '')}")
    return Tool(name="bash", description="run", parameters={"type": "object"}, run=run)


def _agent(script, tools=None, hooks=None, tmp_path=None, **cfg):
    recorder: list = []
    registry = ToolRegistry(tools or [_echo_tool(recorder)])
    agent = Agent(
        llm=FakeLLM(script),
        tools=registry,
        policy=SandboxPolicy(root=tmp_path),
        config=AgentConfig(**cfg),
        hooks=hooks or Hooks(),
    )
    return agent, recorder


class TestBasicFlow:
    def test_plain_answer_ends_the_run(self, tmp_path):
        agent, _ = _agent([AssistantMessage(content="the answer")], tmp_path=tmp_path)
        result = agent.run("question")
        assert result.stop_reason is StopReason.DONE
        assert result.output == "the answer"

    def test_executes_a_tool_then_answers(self, tmp_path):
        script = [
            AssistantMessage(content="", tool_calls=[
                ToolCall(id="c1", name="bash", arguments={"command": "ls"})]),
            AssistantMessage(content="finished"),
        ]
        agent, recorder = _agent(script, tmp_path=tmp_path)
        result = agent.run("go")
        assert recorder == [{"command": "ls"}]
        assert result.output == "finished"

    def test_tool_result_is_fed_back_to_the_model(self, tmp_path):
        script = [
            AssistantMessage(content="", tool_calls=[ToolCall(id="c1", name="bash")]),
            AssistantMessage(content="ok"),
        ]
        agent, _ = _agent(script, tmp_path=tmp_path)
        agent.run("go")
        second_call = agent.llm.calls[1]
        assert any(getattr(m, "tool_call_id", None) == "c1" for m in second_call)

    def test_batched_tool_calls_all_execute_in_one_turn(self, tmp_path):
        """Batching is what keeps turn count below tool-call count -- the
        single biggest lever on cost."""
        script = [
            AssistantMessage(content="", tool_calls=[
                ToolCall(id="c1", name="bash", arguments={"command": "a"}),
                ToolCall(id="c2", name="bash", arguments={"command": "b"}),
                ToolCall(id="c3", name="bash", arguments={"command": "c"}),
            ]),
            AssistantMessage(content="done"),
        ]
        agent, recorder = _agent(script, tmp_path=tmp_path)
        result = agent.run("go")
        assert len(recorder) == 3
        assert result.turns == 2 and result.tool_calls == 3

    def test_every_tool_call_gets_a_result_message(self, tmp_path):
        """The provider rejects a batch where any call lacks its result."""
        script = [
            AssistantMessage(content="", tool_calls=[
                ToolCall(id="c1", name="bash"), ToolCall(id="c2", name="bash")]),
            AssistantMessage(content="done"),
        ]
        agent, _ = _agent(script, tmp_path=tmp_path)
        agent.run("go")
        ids = {getattr(m, "tool_call_id", None) for m in agent.llm.calls[1]}
        assert {"c1", "c2"} <= ids


class TestLimits:
    def test_max_turns_stops_and_says_so(self, tmp_path):
        """Budget exhaustion must be distinguishable from completion, or a
        truncated run gets reported as a finished one."""
        script = [AssistantMessage(content="", tool_calls=[ToolCall(id=f"c{i}", name="bash")])
                  for i in range(10)]
        agent, _ = _agent(script, tmp_path=tmp_path, max_turns=3)
        result = agent.run("go")
        assert result.stop_reason is StopReason.MAX_TURNS
        assert result.turns == 3

    def test_hook_can_stop_the_run(self, tmp_path):
        script = [AssistantMessage(content="", tool_calls=[ToolCall(id="c", name="bash")])] * 5
        hooks = Hooks(should_stop=lambda state: state.turns >= 2)
        agent, _ = _agent(script, hooks=hooks, tmp_path=tmp_path)
        assert agent.run("go").stop_reason is StopReason.STOPPED


class TestTurnBudgetAwareness:
    """The model must know when it is running out of turns.

    Observed live: on a bisection task githi located the correct commit, then
    spent its remaining turns extracting one field per tool call -- sha, then
    PR number, then the diff -- and hit the limit without ever emitting an
    answer it already had. It behaved identically at turn 3 and turn 39
    because nothing told it the budget was nearly gone. A human given "two
    turns left" writes the answer immediately.
    """

    def test_no_warning_early_in_the_run(self, tmp_path):
        """`remaining` rather than `turn` as the marker: the base system
        prompt already says "work in as few turns as possible", so matching
        on "turn" tests the prompt, not the injection."""
        script = [AssistantMessage(content="", tool_calls=[ToolCall(id="c1", name="bash")]),
                  AssistantMessage(content="done")]
        agent, _ = _agent(script, tmp_path=tmp_path, max_turns=30)
        agent.run("go")
        assert "remaining" not in _joined(agent.llm.calls[0]).lower()

    def test_warns_when_the_budget_is_nearly_gone(self, tmp_path):
        """The *last* call is the final-turn message; the countdown warning
        lands on the calls before it."""
        script = [AssistantMessage(content="", tool_calls=[ToolCall(id=f"c{i}", name="bash")])
                  for i in range(6)]
        agent, _ = _agent(script, tmp_path=tmp_path, max_turns=4)
        agent.run("go")
        assert "remaining" in _joined(agent.llm.calls[-2]).lower()
        assert "final turn" in _joined(agent.llm.calls[-1]).lower()

    def test_final_turn_withholds_tools_to_force_an_answer(self, tmp_path):
        """The decisive fix: with no tools on the final turn the model
        physically cannot call one, so it must produce text."""
        script = [AssistantMessage(content="", tool_calls=[ToolCall(id=f"c{i}", name="bash")])
                  for i in range(10)]
        agent, _ = _agent(script, tmp_path=tmp_path, max_turns=3)
        agent.run("go")
        assert agent.llm.tools_seen[-1] == [], "tools must be withheld on the last turn"
        assert agent.llm.tools_seen[0], "...but present on earlier turns"

    def test_answer_on_the_final_turn_counts_as_done(self, tmp_path):
        """A run that delivers its answer at the buzzer completed; reporting
        it as MAX_TURNS would understate a successful run."""
        script = [
            AssistantMessage(content="", tool_calls=[ToolCall(id="c1", name="bash")]),
            AssistantMessage(content="", tool_calls=[ToolCall(id="c2", name="bash")]),
            AssistantMessage(content="the answer is 42"),
        ]
        agent, _ = _agent(script, tmp_path=tmp_path, max_turns=3)
        result = agent.run("go")
        assert result.stop_reason is StopReason.DONE
        assert result.output == "the answer is 42"

    def test_budget_notice_is_not_persisted_into_history(self, tmp_path):
        """Injected per-turn, like any other context transform -- otherwise
        stale 'you have 2 turns left' notices pile up in the transcript."""
        script = [AssistantMessage(content="", tool_calls=[ToolCall(id=f"c{i}", name="bash")])
                  for i in range(6)]
        agent, _ = _agent(script, tmp_path=tmp_path, max_turns=4)
        agent.run("go")
        persisted = " ".join(getattr(m, "content", "") or "" for m in agent.state.messages)
        assert "remaining" not in persisted.lower()


def _joined(messages) -> str:
    return " ".join(getattr(m, "content", "") or "" for m in messages)


class TestRobustness:
    def test_unknown_tool_becomes_an_error_result_and_the_run_continues(self, tmp_path):
        script = [
            AssistantMessage(content="", tool_calls=[ToolCall(id="c1", name="ghost")]),
            AssistantMessage(content="recovered"),
        ]
        agent, _ = _agent(script, tmp_path=tmp_path)
        assert agent.run("go").output == "recovered"

    def test_crashing_tool_does_not_end_the_run(self, tmp_path):
        boom = Tool(name="bash", description="", parameters={},
                    run=lambda a, c: (_ for _ in ()).throw(RuntimeError("kaboom")))
        script = [
            AssistantMessage(content="", tool_calls=[ToolCall(id="c1", name="bash")]),
            AssistantMessage(content="handled"),
        ]
        agent, _ = _agent(script, tools=[boom], tmp_path=tmp_path)
        assert agent.run("go").output == "handled"

    def test_malformed_tool_arguments_become_a_correctable_error(self, tmp_path):
        bad = ToolCall(id="c1", name="bash", arguments={}, malformed=True)
        script = [AssistantMessage(content="", tool_calls=[bad]),
                  AssistantMessage(content="retried")]
        agent, recorder = _agent(script, tmp_path=tmp_path)
        result = agent.run("go")
        assert recorder == [], "a malformed call must not reach the tool"
        assert result.output == "retried"


class TestHooksIntegration:
    def test_before_hook_can_block_a_call(self, tmp_path):
        """Enforcement in the harness, not a request to the model."""
        script = [
            AssistantMessage(content="", tool_calls=[
                ToolCall(id="c1", name="bash", arguments={"command": "rm -rf /"})]),
            AssistantMessage(content="ok"),
        ]
        hooks = Hooks(before_tool_call=lambda ctx: ToolDecision(block=True, reason="denied"))
        agent, recorder = _agent(script, hooks=hooks, tmp_path=tmp_path)
        agent.run("go")
        assert recorder == []

    def test_blocked_call_still_returns_a_tool_result(self, tmp_path):
        """Otherwise the transcript has an orphaned tool_call and the next
        request is rejected by the provider."""
        script = [
            AssistantMessage(content="", tool_calls=[ToolCall(id="c1", name="bash")]),
            AssistantMessage(content="ok"),
        ]
        hooks = Hooks(before_tool_call=lambda ctx: ToolDecision(block=True, reason="denied"))
        agent, _ = _agent(script, hooks=hooks, tmp_path=tmp_path)
        agent.run("go")
        assert any(getattr(m, "tool_call_id", None) == "c1" for m in agent.llm.calls[1])

    def test_transform_context_does_not_accumulate(self, tmp_path):
        """Injected per-turn context must be rebuilt each turn, not appended
        to stored history -- otherwise it stacks up turn after turn."""
        from githi.types import UserMessage
        script = [
            AssistantMessage(content="", tool_calls=[ToolCall(id="c1", name="bash")]),
            AssistantMessage(content="", tool_calls=[ToolCall(id="c2", name="bash")]),
            AssistantMessage(content="done"),
        ]
        hooks = Hooks(transform_context=lambda ms: [UserMessage("BRIEF"), *ms])
        agent, _ = _agent(script, hooks=hooks, tmp_path=tmp_path)
        agent.run("go")
        for sent in agent.llm.calls:
            assert sum(1 for m in sent if getattr(m, "content", "") == "BRIEF") == 1


class TestBookkeeping:
    def test_tracks_usage(self, tmp_path):
        agent, _ = _agent([AssistantMessage(content="x")], tmp_path=tmp_path)
        assert agent.run("go").usage.total.input_tokens == 10

    def test_session_is_persisted_when_configured(self, tmp_path):
        agent, _ = _agent([AssistantMessage(content="x")], tmp_path=tmp_path,
                          session_path=tmp_path / "s.jsonl")
        agent.run("go")
        assert (tmp_path / "s.jsonl").exists()

    def test_system_prompt_is_first(self, tmp_path):
        agent, _ = _agent([AssistantMessage(content="x")], tmp_path=tmp_path)
        agent.run("go")
        from githi.types import SystemMessage
        assert isinstance(agent.llm.calls[0][0], SystemMessage)
