"""Phase 6: child inherits parent subagents.max_depth."""

from __future__ import annotations

from githi.agent.agent import Agent, AgentConfig
from githi.llm.client import Completion
from githi.sandbox.policy import SandboxPolicy
from githi.subagent import SubagentError, assert_can_spawn
from githi.tools.registry import ToolRegistry
from githi.tools.subagent import subagent_tool
from githi.types import AssistantMessage, ToolCall, ToolResultMessage, Usage


class ScriptedLLM:
    def __init__(self, script):
        self.script = list(script)
        self.calls = []
        self.model = "test-model"

    def complete(self, messages, tools, **kw):
        self.calls.append(list(messages))
        message = self.script.pop(0) if self.script else AssistantMessage(content="done")
        return Completion(message=message, usage=Usage(input_tokens=1, output_tokens=1))


class TestMaxDepthInherit:
    def test_child_service_inherits_max_depth(self, tmp_path):
        llm = ScriptedLLM([
            AssistantMessage(content="", tool_calls=[
                ToolCall(id="s1", name="subagent", arguments={"prompt": "nested"}),
            ]),
            # Child tries to spawn again — must fail with DEPTH_EXCEEDED when
            # root max_depth is 1.
            AssistantMessage(content="", tool_calls=[
                ToolCall(id="s2", name="subagent", arguments={"prompt": "too deep"}),
            ]),
            AssistantMessage(content="child noted depth block"),
            AssistantMessage(content="parent done"),
        ])
        agent = Agent(
            llm=llm,
            tools=ToolRegistry([subagent_tool()]),
            policy=SandboxPolicy(root=tmp_path),
            config=AgentConfig(cwd=str(tmp_path)),
        )
        agent.subagents.max_depth = 1
        captured = {}
        original = agent.subagents._build_child

        def capture(**kwargs):
            child = original(**kwargs)
            captured["max_depth"] = child.subagents.max_depth
            return child

        agent.subagents._build_child = capture
        result = agent.run("go")
        assert captured["max_depth"] == 1
        assert result.output == "parent done"
        parent_tools = [
            m for m in agent.state.messages if isinstance(m, ToolResultMessage)
        ]
        assert "child noted depth block" in parent_tools[0].content

    def test_assert_can_spawn_respects_inherited_cap(self):
        assert_can_spawn(0, 1)
        try:
            assert_can_spawn(1, 1)
            assert False, "expected DEPTH_EXCEEDED"
        except SubagentError as exc:
            assert exc.code == "DEPTH_EXCEEDED"
