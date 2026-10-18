"""One-shot foreground subagent (DeepSeek-style minimal slice)."""

from __future__ import annotations

import pytest

from githi.agent.agent import Agent, AgentConfig
from githi.agent.interrupt import Interrupt
from githi.llm.client import Completion
from githi.sandbox.policy import SandboxPolicy
from githi.subagent import (
    DEFAULT_MAX_DEPTH,
    SubagentError,
    SubagentRunResult,
    assert_can_spawn,
    delegation_depth_of,
    map_run_to_outcome,
)
from githi.tools.ask_user import AskAnswer, AskAnswerItem, ask_user_tool
from githi.tools.base import Tool, ToolContext, ok
from githi.tools.registry import ToolRegistry, default_tools
from githi.tools.subagent import subagent_tool
from githi.types import AssistantMessage, StopReason, ToolCall, ToolResultMessage, Usage


class ScriptedLLM:
    def __init__(self, script):
        self.script = list(script)
        self.calls: list[list] = []
        self.model = "test-model"

    def complete(self, messages, tools, **kw):
        self.calls.append(list(messages))
        message = self.script.pop(0) if self.script else AssistantMessage(content="done")
        return Completion(message=message, usage=Usage(input_tokens=1, output_tokens=1))


def _code(result) -> str | None:
    details = result.details or {}
    return details.get("code") if isinstance(details, dict) else None


def _ctx(**extra):
    return ToolContext(policy=SandboxPolicy(root="/tmp"), extra=dict(extra))


class TestDepthHelpers:
    def test_delegation_depth_defaults_to_zero(self):
        assert delegation_depth_of(None) == 0
        assert delegation_depth_of({}) == 0

    def test_assert_can_spawn_at_cap(self):
        assert_can_spawn(2, 3)  # child would be 3
        with pytest.raises(SubagentError) as caught:
            assert_can_spawn(3, 3)
        assert caught.value.code == "DEPTH_EXCEEDED"

    def test_max_depth_zero_forbids_all(self):
        with pytest.raises(SubagentError) as caught:
            assert_can_spawn(0, 0)
        assert caught.value.code == "DEPTH_EXCEEDED"

    def test_default_max_depth_matches_deepseek(self):
        assert DEFAULT_MAX_DEPTH == 3


class TestDelegatedAskUser:
    def test_delegated_beats_provider(self):
        def ask(qs):
            return AskAnswer(answers=[
                AskAnswerItem(id=qs[0].id, selected=["Yes"])])

        result = ToolRegistry([ask_user_tool()]).execute(
            "ask_user_question",
            {"questions": [{
                "id": "q1", "question": "Ship?",
                "options": [{"label": "Yes"}, {"label": "No"}],
            }]},
            _ctx(delegated=True, ask_user=ask),
        )
        assert result.is_error
        assert _code(result) == "DELEGATED_CALLER"
        assert "owned by another" in result.text


class TestMapOutcome:
    def test_done_with_text(self):
        ok_flag, text = map_run_to_outcome(SubagentRunResult(
            id="x", output="hello", stop_reason=StopReason.DONE))
        assert ok_flag and text == "hello"

    def test_done_empty_fails(self):
        ok_flag, text = map_run_to_outcome(SubagentRunResult(
            id="x", output="  ", stop_reason=StopReason.DONE))
        assert not ok_flag
        assert "without producing" in text

    def test_interrupted_keeps_partial(self):
        ok_flag, text = map_run_to_outcome(SubagentRunResult(
            id="x", output="halfway", stop_reason=StopReason.INTERRUPTED))
        assert not ok_flag
        assert "cancelled" in text
        assert "halfway" in text


class TestSubagentTool:
    def test_missing_spawner(self):
        result = ToolRegistry([subagent_tool()]).execute(
            "subagent", {"prompt": "do thing"}, _ctx())
        assert result.is_error
        assert _code(result) == "NO_PROVIDER"

    def test_empty_prompt(self):
        result = ToolRegistry([subagent_tool()]).execute(
            "subagent", {"prompt": "  "},
            _ctx(spawn_subagent=lambda *a, **k: None))
        assert result.is_error
        assert _code(result) == "BAD_ARGS"

    def test_depth_exceeded_via_service(self, tmp_path):
        parent = Agent(
            llm=ScriptedLLM([AssistantMessage(content="unused")]),
            tools=ToolRegistry([subagent_tool()]),
            policy=SandboxPolicy(root=tmp_path),
            config=AgentConfig(cwd=str(tmp_path)),
        )
        parent.subagents.max_depth = 1
        result = ToolRegistry([subagent_tool()]).execute(
            "subagent",
            {"prompt": "nested", "description": "x"},
            _ctx(
                spawn_subagent=parent.subagents.start_one_shot,
                delegation_depth=1,
            ),
        )
        assert result.is_error
        assert _code(result) == "DEPTH_EXCEEDED"


class TestNestedSpawn:
    def test_parent_gets_only_child_final_text(self, tmp_path):
        # Shared script: parent calls subagent, then child answers, then parent
        # answers. Both agents share the same LLM queue.
        llm = ScriptedLLM([
            AssistantMessage(content="", tool_calls=[
                ToolCall(id="s1", name="subagent",
                         arguments={"prompt": "summarize X",
                                    "description": "research"})]),
            AssistantMessage(content="child summary of X"),
            AssistantMessage(content="parent done"),
        ])
        tools = ToolRegistry([subagent_tool()])
        agent = Agent(
            llm=llm,
            tools=tools,
            policy=SandboxPolicy(root=tmp_path),
            config=AgentConfig(
                cwd=str(tmp_path),
                tools_for_prompt=list(tools._tools.values()),
            ),
        )
        result = agent.run("please delegate")
        assert result.stop_reason is StopReason.DONE
        assert result.output == "parent done"
        tool_msgs = [m for m in agent.state.messages
                     if isinstance(m, ToolResultMessage)]
        assert len(tool_msgs) == 1
        assert tool_msgs[0].content == "child summary of X"
        assert not tool_msgs[0].is_error

    def test_child_is_delegated_for_ask_user(self, tmp_path):
        llm = ScriptedLLM([
            AssistantMessage(content="", tool_calls=[
                ToolCall(id="s1", name="subagent",
                         arguments={"prompt": "ask someone"})]),
            AssistantMessage(content="", tool_calls=[
                ToolCall(id="a1", name="ask_user_question",
                         arguments={"questions": [{
                             "id": "q", "question": "ok?",
                             "options": [{"label": "Yes"}],
                         }]})]),
            AssistantMessage(content="child noted the block"),
            AssistantMessage(content="parent saw child"),
        ])
        tools = ToolRegistry([subagent_tool(), ask_user_tool()])
        agent = Agent(
            llm=llm,
            tools=tools,
            policy=SandboxPolicy(root=tmp_path),
            config=AgentConfig(cwd=str(tmp_path)),
        )
        # Even if parent has a UI asker, the child must not use it.
        agent.ask_user = lambda qs: AskAnswer(answers=[
            AskAnswerItem(id=qs[0].id, selected=["Yes"])])
        result = agent.run("delegate an ask")
        assert result.stop_reason is StopReason.DONE
        parent_tools = [m for m in agent.state.messages
                        if isinstance(m, ToolResultMessage)]
        assert len(parent_tools) == 1
        assert "child noted the block" in parent_tools[0].content

    def test_shared_interrupt_stops_child(self, tmp_path):
        flag = Interrupt()

        def child_bash(args, ctx):
            flag.set()
            return ok("ran")

        llm = ScriptedLLM([
            AssistantMessage(content="", tool_calls=[
                ToolCall(id="s1", name="subagent",
                         arguments={"prompt": "work"})]),
            AssistantMessage(content="", tool_calls=[
                ToolCall(id="b1", name="bash", arguments={}),
                ToolCall(id="b2", name="bash", arguments={}),
            ]),
            AssistantMessage(content="should not reach parent answer"),
        ])
        tools = ToolRegistry([
            subagent_tool(),
            Tool(name="bash", description="d", parameters={"type": "object"},
                 run=child_bash),
        ])
        agent = Agent(
            llm=llm,
            tools=tools,
            policy=SandboxPolicy(root=tmp_path),
            config=AgentConfig(cwd=str(tmp_path), interrupt=flag),
        )
        result = agent.run("go")
        tool_msgs = [m for m in agent.state.messages
                     if isinstance(m, ToolResultMessage)]
        assert tool_msgs
        text = tool_msgs[0].content.lower()
        assert tool_msgs[0].is_error or "interrupt" in text or "cancelled" in text
        assert result.stop_reason in (
            StopReason.DONE, StopReason.INTERRUPTED, StopReason.ERROR,
            StopReason.MAX_TURNS, StopReason.STOPPED)


class TestDefaultToolsIncludeSubagent:
    def test_registry_lists_subagent(self):
        assert "subagent" in ToolRegistry(default_tools()).names()
