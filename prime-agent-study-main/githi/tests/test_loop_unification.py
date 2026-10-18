"""One loop, not two.

githi ended up with two agent loops: `run_loop`, the faithful port of pi's
nested outer/inner structure, and the inline `while True` inside `Agent.run`.
Each grew capabilities the other lacks, so behaviour depends on which entry
point a caller happens to use -- and `run_loop` was reached only by its own
tests, meaning the code exercised in production was the *less* capable one.

That is worse than either loop alone. A fix or a hardening applied to one
silently does not apply to the other, and no test catches the divergence
because both suites pass.

These tests pin the union. `Agent.run` must gain what only `run_loop` had:

* `prepare_next_turn` swapping context/model/thinking between turns -- the
  hook a phase machine is built on
* truncated responses (`finish_reason == "length"`) failed rather than
  executed, because clipped JSON arguments may be half-written
* provider errors ending the run instead of looping

and `run_loop` must gain what only `Agent.run` had: compaction, the turn
budget notices, and session persistence.
"""

from __future__ import annotations

import pytest

from githi.agent.agent import Agent, AgentConfig
from githi.agent.hooks import Hooks
from githi.llm.client import Completion
from githi.sandbox.policy import SandboxPolicy
from githi.tools.base import Tool, ToolResult
from githi.tools.registry import ToolRegistry
from githi.types import (
    AgentContext,
    AgentLoopTurnUpdate,
    AssistantMessage,
    StopReason,
    ThinkingLevel,
    ToolCall,
    ToolResultMessage,
    Usage,
)


class ScriptedLLM:
    def __init__(self, script, finish_reasons=None):
        self.script = list(script)
        self.finish_reasons = list(finish_reasons or [])
        self.calls: list[list] = []
        self.kwargs: list[dict] = []
        self.tools_seen: list[list] = []
        self.model = "test-model"

    def complete(self, messages, tools, **kw):
        self.calls.append(list(messages))
        self.kwargs.append(dict(kw))
        self.tools_seen.append(list(tools or []))
        message = self.script.pop(0) if self.script else AssistantMessage(content="done")
        finish = self.finish_reasons.pop(0) if self.finish_reasons else "stop"
        return Completion(message=message, usage=Usage(input_tokens=1),
                          finish_reason=finish)


def _tool(name="bash", output="ran"):
    return Tool(name=name, description="d", parameters={"type": "object"},
                run=lambda a, c: ToolResult(content=output))


def _agent(tmp_path, script, finish_reasons=None, hooks=None, **cfg):
    return Agent(llm=ScriptedLLM(script, finish_reasons),
                 tools=ToolRegistry([_tool()]),
                 policy=SandboxPolicy(root=tmp_path),
                 hooks=hooks or Hooks(),
                 config=AgentConfig(**cfg))


def _call(i):
    return AssistantMessage(content="", tool_calls=[ToolCall(f"c{i}", "bash", {})])


class TestAgentGainsPrepareNextTurn:
    def test_prepare_next_turn_can_swap_the_system_prompt(self, tmp_path):
        """The phase machine: returning a new context changes the prompt for
        the following turn. Without it a hook that swaps context does nothing
        and fails silently."""
        swapped = AgentContext(system_prompt="PHASE TWO", messages=[])

        def prepare(ctx):
            return AgentLoopTurnUpdate(context=swapped)

        agent = _agent(tmp_path, [_call(0), AssistantMessage(content="done")],
                       hooks=Hooks(prepare_next_turn=prepare))
        agent.run("go")
        assert any("PHASE TWO" in getattr(m, "content", "") or ""
                   for m in agent.llm.calls[-1])

    def test_prepare_next_turn_can_raise_the_thinking_level(self, tmp_path):
        def prepare(ctx):
            return AgentLoopTurnUpdate(thinking_level=ThinkingLevel.HIGH)

        agent = _agent(tmp_path, [_call(0), AssistantMessage(content="done")],
                       hooks=Hooks(prepare_next_turn=prepare))
        agent.run("go")
        assert agent.llm.kwargs[-1].get("thinking_level") is ThinkingLevel.HIGH

    def test_prepare_next_turn_can_switch_model(self, tmp_path):
        def prepare(ctx):
            return AgentLoopTurnUpdate(model="bigger-model")

        agent = _agent(tmp_path, [_call(0), AssistantMessage(content="done")],
                       hooks=Hooks(prepare_next_turn=prepare))
        agent.run("go")
        assert agent.llm.kwargs[-1].get("model") == "bigger-model"

    def test_returning_none_keeps_everything(self, tmp_path):
        agent = _agent(tmp_path, [_call(0), AssistantMessage(content="done")],
                       hooks=Hooks(prepare_next_turn=lambda ctx: None))
        assert agent.run("go").stop_reason is StopReason.DONE


class TestAgentGainsTruncationHandling:
    def test_a_truncated_response_does_not_execute_its_tool_calls(self, tmp_path):
        """`length` means the provider cut generation off mid-JSON. Running a
        clipped argument is how a path gets deleted that nobody named."""
        executed = []
        tool = Tool(name="bash", description="d", parameters={"type": "object"},
                    run=lambda a, c: executed.append(a) or ToolResult(content="ran"))
        agent = Agent(llm=ScriptedLLM([_call(0), AssistantMessage(content="done")],
                                      finish_reasons=["length", "stop"]),
                      tools=ToolRegistry([tool]),
                      policy=SandboxPolicy(root=tmp_path), config=AgentConfig())
        agent.run("go")
        assert executed == []

    def test_a_truncated_response_still_answers_every_tool_call(self, tmp_path):
        """Leaving a tool_call without a result makes the next request invalid
        on the wire, so refusing to execute must still produce results."""
        agent = Agent(llm=ScriptedLLM([_call(0), AssistantMessage(content="done")],
                                      finish_reasons=["length", "stop"]),
                      tools=ToolRegistry([_tool()]),
                      policy=SandboxPolicy(root=tmp_path), config=AgentConfig())
        agent.run("go")
        results = [m for m in agent.state.messages if isinstance(m, ToolResultMessage)]
        assert len(results) == 1 and results[0].is_error

    def test_a_provider_error_ends_the_run(self, tmp_path):
        agent = Agent(llm=ScriptedLLM([AssistantMessage(content="")],
                                      finish_reasons=["error"]),
                      tools=ToolRegistry([_tool()]),
                      policy=SandboxPolicy(root=tmp_path), config=AgentConfig())
        assert agent.run("go").stop_reason is StopReason.ERROR


class TestPreservedAgentBehaviour:
    """Everything `Agent.run` already did must survive the merge."""

    def test_the_final_turn_withholds_tools(self, tmp_path):
        agent = _agent(tmp_path, [_call(i) for i in range(5)], max_turns=2)
        agent.run("go")
        assert agent.llm.calls and not _tools_of(agent, -1)

    def test_the_budget_warning_is_not_written_into_the_transcript(self, tmp_path):
        """Appended to the per-turn view only -- a stale '2 turns remaining'
        lingering in the transcript misinforms every later turn."""
        agent = _agent(tmp_path, [_call(i) for i in range(6)], max_turns=4,
                       warn_turns_remaining=3)
        agent.run("go")
        assert not any("turns remaining" in (getattr(m, "content", "") or "")
                       for m in agent.state.messages)

    def test_max_turns_is_distinct_from_done(self, tmp_path):
        agent = _agent(tmp_path, [_call(i) for i in range(10)], max_turns=3,
                       force_answer_on_final_turn=False)
        assert agent.run("go").stop_reason is StopReason.MAX_TURNS

    def test_steering_still_reaches_the_next_turn(self, tmp_path):
        from githi.types import UserMessage
        agent = _agent(tmp_path, [_call(0), AssistantMessage(content="done")])
        agent.steer(UserMessage("use the other API"))
        agent.run("go")
        assert any("use the other API" in (getattr(m, "content", "") or "")
                   for m in agent.state.messages)

    def test_follow_up_still_restarts_a_finished_run(self, tmp_path):
        from githi.types import UserMessage
        agent = _agent(tmp_path, [AssistantMessage(content="first"),
                                  AssistantMessage(content="second")])
        agent.follow_up(UserMessage("one more thing"))
        agent.run("go")
        assert len(agent.llm.calls) == 2

    def test_terminate_still_stops_the_run(self, tmp_path):
        stopper = Tool(name="bash", description="d", parameters={"type": "object"},
                       run=lambda a, c: ToolResult(content="bye", terminate=True))
        agent = Agent(llm=ScriptedLLM([_call(0), AssistantMessage(content="unreached")]),
                      tools=ToolRegistry([stopper]),
                      policy=SandboxPolicy(root=tmp_path), config=AgentConfig())
        agent.run("go")
        assert len(agent.llm.calls) == 1

    def test_compaction_still_runs(self, tmp_path):
        """Needs a bulky tool: a three-character result never approaches any
        realistic budget, so a small-output fixture would pass whether or not
        compaction was still wired."""
        calls = []
        agent = Agent(
            llm=ScriptedLLM([_call(i) for i in range(12)]
                            + [AssistantMessage(content="done")]),
            tools=ToolRegistry([_tool(output="x" * 4000)]),
            policy=SandboxPolicy(root=tmp_path),
            config=AgentConfig(
                max_turns=14, context_limit_tokens=800,
                summarizer=lambda m, previous_summary=None:
                    calls.append(1) or "## Goal\nx"))
        agent.run("go")
        assert calls, "compaction stopped running after the merge"

    def test_session_persistence_still_happens(self, tmp_path):
        session = tmp_path / "s.jsonl"
        agent = _agent(tmp_path, [AssistantMessage(content="done")],
                       session_path=session)
        agent.run("go")
        assert session.exists() and session.read_text().strip()


def _tools_of(agent, index):
    """The tool schemas passed on a given request."""
    return agent.llm.tools_seen[index]


class TestSingleImplementation:
    def test_agent_run_delegates_to_run_loop(self):
        """The structural check. Two loops drift; one cannot."""
        import inspect

        from githi.agent import agent as agent_module

        source = inspect.getsource(agent_module.Agent.run)
        assert "run_loop" in source, "Agent.run still has its own inline loop"
