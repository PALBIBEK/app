"""list_agents — continuable child inventory for the parent model."""

from __future__ import annotations

import re
import threading
import time

from githi.agent.agent import Agent, AgentConfig
from githi.llm.client import Completion
from githi.sandbox.policy import SandboxPolicy
from githi.tools.base import ToolContext
from githi.tools.list_agents import list_agents_tool
from githi.tools.registry import ToolRegistry, default_tools
from githi.tools.subagent_continuable import subagent_continuable_tool
from githi.types import (
    AssistantMessage,
    ToolCall,
    ToolResultMessage,
    Usage,
)


class ThreadSafeScriptedLLM:
    def __init__(self, script, *, child_gate: threading.Event | None = None):
        self.script = list(script)
        self.calls: list[list] = []
        self.model = "test-model"
        self._lock = threading.Lock()
        self._child_gate = child_gate

    def complete(self, messages, tools, **kw):
        is_child = threading.current_thread().name.startswith("githi-continuable")
        if is_child and self._child_gate is not None:
            assert self._child_gate.wait(timeout=5), "child gate timed out"
        with self._lock:
            self.calls.append(list(messages))
            message = (
                self.script.pop(0) if self.script
                else AssistantMessage(content="done")
            )
            return Completion(
                message=message,
                usage=Usage(input_tokens=1, output_tokens=1),
            )


def _child_id_from_ack(text: str) -> str:
    match = re.search(r"started subagent (\w+)", text)
    assert match, text
    return match.group(1)


def _wait_settled(agent: Agent, child_id: str, timeout: float = 5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        child = agent.subagent_continuable.get(child_id)
        if child is not None and child.status == "ready":
            return child
        time.sleep(0.01)
    raise AssertionError(f"child {child_id} not ready after {timeout}s")


class TestListAgentsEmpty:
    def test_empty_render(self, tmp_path):
        agent = Agent(
            llm=ThreadSafeScriptedLLM([]),
            tools=ToolRegistry([list_agents_tool()]),
            policy=SandboxPolicy(root=tmp_path),
            config=AgentConfig(cwd=str(tmp_path)),
        )
        result = agent.tools.execute(
            "list_agents",
            {},
            ToolContext(
                policy=SandboxPolicy(root=tmp_path),
                extra={"subagent_continuable": agent.subagent_continuable},
            ),
        )
        assert not result.is_error
        assert result.text == "(no subagents)"
        assert result.details["count"] == 0


class TestListAgentsStatuses:
    def test_running_then_idle(self, tmp_path):
        gate = threading.Event()
        llm = ThreadSafeScriptedLLM(
            [
                AssistantMessage(content="", tool_calls=[
                    ToolCall(
                        id="c1",
                        name="subagent_continuable",
                        arguments={
                            "prompt": "work",
                            "description": "survey",
                        },
                    ),
                ]),
                AssistantMessage(content="parent after start"),
                AssistantMessage(content="child done"),
            ],
            child_gate=gate,
        )
        tools = ToolRegistry([
            subagent_continuable_tool(),
            list_agents_tool(),
        ])
        agent = Agent(
            llm=llm,
            tools=tools,
            policy=SandboxPolicy(root=tmp_path),
            config=AgentConfig(cwd=str(tmp_path)),
        )
        agent.run("go")
        ack = next(
            m for m in agent.state.messages if isinstance(m, ToolResultMessage)
        )
        child_id = _child_id_from_ack(ack.content)

        running = tools.execute(
            "list_agents",
            {},
            ToolContext(
                policy=SandboxPolicy(root=tmp_path),
                extra={"subagent_continuable": agent.subagent_continuable},
            ),
        )
        assert not running.is_error
        assert f"{child_id} [running]" in running.text
        assert "survey" in running.text
        assert running.details["agents"][0]["status"] == "running"

        gate.set()
        _wait_settled(agent, child_id)

        idle = tools.execute(
            "list_agents",
            {},
            ToolContext(
                policy=SandboxPolicy(root=tmp_path),
                extra={"subagent_continuable": agent.subagent_continuable},
            ),
        )
        assert f"{child_id} [ready]" in idle.text
        assert idle.details["agents"][0]["status"] == "ready"
        assert idle.details["agents"][0]["kind"] == "child"


class TestListAgentsRegistry:
    def test_in_default_tools(self):
        assert "list_agents" in ToolRegistry(default_tools()).names()

    def test_no_provider(self, tmp_path):
        result = ToolRegistry([list_agents_tool()]).execute(
            "list_agents",
            {},
            ToolContext(policy=SandboxPolicy(root=tmp_path), extra={}),
        )
        assert result.is_error
        assert "NO_PROVIDER" in result.text
