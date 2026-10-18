"""Phase 4: children must not inherit parent-only control tools."""

from __future__ import annotations

from githi.agent.agent import Agent, AgentConfig
from githi.llm.client import Completion
from githi.sandbox.policy import SandboxPolicy
from githi.subagent import PARENT_ONLY_TOOLS
from githi.tools.registry import ToolRegistry, default_tools
from githi.tools.subagent import subagent_tool
from githi.tools.subagent_continuable import subagent_continuable_tool
from githi.types import AssistantMessage, ToolCall, Usage


class ScriptedLLM:
    def __init__(self, script):
        self.script = list(script)
        self.calls = []
        self.model = "test-model"

    def complete(self, messages, tools, **kw):
        self.calls.append(list(messages))
        message = self.script.pop(0) if self.script else AssistantMessage(content="done")
        return Completion(message=message, usage=Usage(input_tokens=1, output_tokens=1))


class TestChildToolFilter:
    def test_one_shot_child_excludes_parent_only_tools(self, tmp_path):
        llm = ScriptedLLM([
            AssistantMessage(content="", tool_calls=[
                ToolCall(id="s1", name="subagent", arguments={"prompt": "x"}),
            ]),
            AssistantMessage(content="child done"),
            AssistantMessage(content="parent done"),
        ])
        agent = Agent(
            llm=llm,
            tools=ToolRegistry(default_tools()),
            policy=SandboxPolicy(root=tmp_path),
            config=AgentConfig(cwd=str(tmp_path)),
        )
        # Capture child tools via a wrapper around start_one_shot
        seen = {}
        original = agent.subagents.start_one_shot

        def wrapped(*args, **kwargs):
            result = original(*args, **kwargs)
            # Child is gone after start returns; inspect via rebuild
            return result

        # Instead inspect during build:
        original_build = agent.subagents._build_child

        def capture_build(**kwargs):
            child = original_build(**kwargs)
            seen["names"] = child.tools.names()
            return child

        agent.subagents._build_child = capture_build
        agent.run("go")
        names = set(seen["names"])
        for banned in PARENT_ONLY_TOOLS:
            assert banned not in names, banned
        assert "subagent" in names
        assert "subagent_fork" in names

    def test_continuable_child_gets_report_not_send_message(self, tmp_path):
        llm = ScriptedLLM([
            AssistantMessage(content="", tool_calls=[
                ToolCall(
                    id="c1",
                    name="subagent_continuable",
                    arguments={"prompt": "x"},
                ),
            ]),
            AssistantMessage(content="parent done"),
            AssistantMessage(content="child done"),
        ])
        agent = Agent(
            llm=llm,
            tools=ToolRegistry(default_tools()),
            policy=SandboxPolicy(root=tmp_path),
            config=AgentConfig(cwd=str(tmp_path)),
        )
        agent.run("go")
        # Find the continuable child (may be parked)
        children = agent.subagent_continuable.list_children()
        assert children
        child = agent.subagent_continuable.get(children[0]["id"])
        assert child is not None
        # Rematerialize to inspect tools if parked
        if child.agent is None:
            with child.lock:
                agent.subagent_continuable._materialize_locked(child)
        names = set(child.agent.tools.names())
        assert "report" in names
        assert "send_message" not in names
        assert "list_agents" not in names
        assert "job_output" not in names
