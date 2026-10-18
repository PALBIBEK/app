"""Phase-2 subagent_fork: completed-turn seed + foreground await."""

from __future__ import annotations

from githi.agent.agent import Agent, AgentConfig
from githi.llm.client import Completion
from githi.sandbox.policy import SandboxPolicy
from githi.subagent.seed import completed_turn_prefix, open_tool_turn_index
from githi.tools.registry import ToolRegistry, default_tools
from githi.tools.subagent import subagent_tool
from githi.tools.subagent_fork import subagent_fork_tool
from githi.types import (
    AssistantMessage,
    StopReason,
    SystemMessage,
    ToolCall,
    ToolResultMessage,
    UserMessage,
    Usage,
)


class ScriptedLLM:
    def __init__(self, script):
        self.script = list(script)
        self.calls: list[list] = []
        self.model = "test-model"

    def complete(self, messages, tools, **kw):
        self.calls.append(list(messages))
        message = self.script.pop(0) if self.script else AssistantMessage(content="done")
        return Completion(message=message, usage=Usage(input_tokens=1, output_tokens=1))


class TestCompletedTurnPrefix:
    def test_open_batch_cuts_before_assistant_and_its_user(self):
        messages = [
            SystemMessage("sys"),
            UserMessage("first"),
            AssistantMessage(content="ok"),
            UserMessage("second"),
            AssistantMessage(
                content="",
                tool_calls=[ToolCall(id="f1", name="subagent_fork", arguments={})],
            ),
        ]
        assert open_tool_turn_index(messages) == 4
        prefix = completed_turn_prefix(messages)
        assert [type(m) for m in prefix] == [SystemMessage, UserMessage, AssistantMessage]
        assert prefix[1].content == "first"
        assert prefix[2].content == "ok"

    def test_keeps_answered_tool_turns(self):
        messages = [
            SystemMessage("sys"),
            UserMessage("go"),
            AssistantMessage(
                content="",
                tool_calls=[ToolCall(id="b1", name="bash", arguments={})],
            ),
            ToolResultMessage(tool_call_id="b1", content="ran"),
            UserMessage("now fork"),
            AssistantMessage(
                content="",
                tool_calls=[ToolCall(id="f1", name="subagent_fork", arguments={})],
            ),
        ]
        prefix = completed_turn_prefix(messages)
        assert isinstance(prefix[-1], ToolResultMessage)
        assert prefix[-1].tool_call_id == "b1"
        assert not any(
            isinstance(m, AssistantMessage) and m.tool_calls
            and m.tool_calls[0].id == "f1"
            for m in prefix
        )

    def test_first_open_turn_leaves_system_only(self):
        messages = [
            SystemMessage("sys"),
            UserMessage("only"),
            AssistantMessage(
                content="",
                tool_calls=[ToolCall(id="f1", name="subagent_fork", arguments={})],
            ),
        ]
        prefix = completed_turn_prefix(messages)
        assert len(prefix) == 1
        assert isinstance(prefix[0], SystemMessage)

    def test_deepcopy_isolates_parent(self):
        messages = [
            SystemMessage("sys"),
            UserMessage("a"),
            AssistantMessage(content="b"),
            UserMessage("open"),
            AssistantMessage(
                content="",
                tool_calls=[ToolCall(id="f1", name="subagent_fork", arguments={})],
            ),
        ]
        prefix = completed_turn_prefix(messages)
        prefix[1].content = "mutated"
        assert messages[1].content == "a"


class TestSubagentForkIntegration:
    def test_child_sees_prior_history_not_open_turn(self, tmp_path):
        llm = ScriptedLLM([
            AssistantMessage(content="prior answer"),
            AssistantMessage(content="", tool_calls=[
                ToolCall(id="f1", name="subagent_fork",
                         arguments={"prompt": "continue from that",
                                    "description": "fork"})]),
            AssistantMessage(content="child used prior context"),
            AssistantMessage(content="parent wrapped"),
        ])
        tools = ToolRegistry([subagent_fork_tool()])
        agent = Agent(
            llm=llm,
            tools=tools,
            policy=SandboxPolicy(root=tmp_path),
            config=AgentConfig(cwd=str(tmp_path)),
        )
        # First run builds history: user + prior answer.
        r1 = agent.run("remember the color blue")
        assert r1.output == "prior answer"
        # Second run forks with history.
        r2 = agent.run("please fork")
        assert r2.stop_reason is StopReason.DONE
        assert r2.output == "parent wrapped"

        # Child's first complete call is the third LLM call overall.
        child_msgs = llm.calls[2]
        roles = [getattr(m, "content", None) for m in child_msgs]
        # Prior user + assistant text must be present; open "please fork" must not.
        contents = []
        for m in child_msgs:
            if hasattr(m, "content"):
                contents.append(m.content)
        assert "remember the color blue" in contents
        assert "prior answer" in contents
        assert "continue from that" in contents
        assert "please fork" not in contents

        tool_msgs = [m for m in agent.state.messages
                     if isinstance(m, ToolResultMessage)]
        assert tool_msgs[-1].content == "child used prior context"
        assert not tool_msgs[-1].is_error

    def test_fork_isolation_from_parent_messages(self, tmp_path):
        llm = ScriptedLLM([
            AssistantMessage(content="hello"),
            AssistantMessage(content="", tool_calls=[
                ToolCall(id="f1", name="subagent_fork",
                         arguments={"prompt": "say hi"})]),
            AssistantMessage(content="child hi"),
            AssistantMessage(content="parent hi"),
        ])
        agent = Agent(
            llm=llm,
            tools=ToolRegistry([subagent_fork_tool()]),
            policy=SandboxPolicy(root=tmp_path),
            config=AgentConfig(cwd=str(tmp_path)),
        )
        agent.run("start")
        before = [m.content if hasattr(m, "content") else m
                  for m in agent.state.messages]
        agent.run("fork now")
        # Parent still has its original "hello" assistant text unchanged.
        assert any(
            isinstance(m, AssistantMessage) and m.content == "hello"
            for m in agent.state.messages
        )
        assert before[0]  # sanity


class TestRegistryFork:
    def test_default_tools_include_fork(self):
        names = ToolRegistry(default_tools()).names()
        assert "subagent" in names
        assert "subagent_fork" in names
        assert "subagent" in ToolRegistry([subagent_tool()]).names()
