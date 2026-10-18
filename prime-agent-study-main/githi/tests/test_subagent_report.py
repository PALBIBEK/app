"""Continuable child `report` tool — framed relay to the live parent."""

from __future__ import annotations

import re
import threading
import time

from githi.agent.agent import Agent, AgentConfig
from githi.llm.client import Completion
from githi.sandbox.policy import SandboxPolicy
from githi.subagent.continuable import format_report_notice
from githi.tools.base import ToolContext
from githi.tools.registry import ToolRegistry, default_tools
from githi.tools.report import report_tool
from githi.tools.subagent import subagent_tool
from githi.tools.subagent_continuable import subagent_continuable_tool
from githi.types import (
    AssistantMessage,
    ToolCall,
    ToolResultMessage,
    Usage,
    UserMessage,
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


def _code(result) -> str | None:
    details = result.details or {}
    return details.get("code") if isinstance(details, dict) else None


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


def _drain_notices(agent: Agent, predicate, timeout: float = 5.0) -> list[UserMessage]:
    """Wait for matching notices without discarding unrelated queued messages."""
    found: list[UserMessage] = []
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        for queue in (agent._follow_up, agent._next_turn):
            batch = queue.drain()
            keep: list[UserMessage] = []
            for msg in batch:
                assert isinstance(msg, UserMessage)
                if predicate(msg.content):
                    found.append(msg)
                else:
                    keep.append(msg)
            for msg in keep:
                queue.enqueue(msg)
        if found:
            return found
        time.sleep(0.01)
    return found


class TestReportDelivery:
    def test_report_reaches_parent_before_settle(self, tmp_path):
        gate = threading.Event()
        llm = ThreadSafeScriptedLLM(
            [
                AssistantMessage(content="", tool_calls=[
                    ToolCall(
                        id="c1",
                        name="subagent_continuable",
                        arguments={"prompt": "research"},
                    ),
                ]),
                AssistantMessage(content="parent after start"),
                # Child: report, then finish.
                AssistantMessage(content="", tool_calls=[
                    ToolCall(
                        id="r1",
                        name="report",
                        arguments={"output": "found the README"},
                    ),
                ]),
                AssistantMessage(content="child closing remark"),
            ],
            child_gate=gate,
        )
        agent = Agent(
            llm=llm,
            tools=ToolRegistry([subagent_continuable_tool()]),
            policy=SandboxPolicy(root=tmp_path),
            config=AgentConfig(cwd=str(tmp_path)),
        )
        agent.run("start")
        ack = next(
            m for m in agent.state.messages if isinstance(m, ToolResultMessage)
        )
        child_id = _child_id_from_ack(ack.content)
        child = agent.subagent_continuable.get(child_id)
        assert child is not None
        assert "report" in child.agent.tools.names()

        gate.set()
        reports = _drain_notices(
            agent,
            lambda text: f"Background subagent {child_id} reported:" in text,
        )
        assert len(reports) == 1
        assert "found the README" in reports[0].content
        # Report must not have settled the child by itself; wait for idle.
        settled = _wait_settled(agent, child_id)
        assert settled.last_output == "child closing remark"
        settlements = _drain_notices(
            agent,
            lambda text: (
                f"Background subagent {child_id}" in text
                and "reported:" not in text
                and "will do no further work" in text
            ),
        )
        assert len(settlements) == 1
        assert "child closing remark" in settlements[0].content

    def test_report_ack_includes_message_id(self, tmp_path):
        gate = threading.Event()
        llm = ThreadSafeScriptedLLM(
            [
                AssistantMessage(content="", tool_calls=[
                    ToolCall(
                        id="c1",
                        name="subagent_continuable",
                        arguments={"prompt": "x"},
                    ),
                ]),
                AssistantMessage(content="parent done"),
                AssistantMessage(content="", tool_calls=[
                    ToolCall(
                        id="r1",
                        name="report",
                        arguments={"output": "payload"},
                    ),
                ]),
                AssistantMessage(content="child done"),
            ],
            child_gate=gate,
        )
        agent = Agent(
            llm=llm,
            tools=ToolRegistry([subagent_continuable_tool()]),
            policy=SandboxPolicy(root=tmp_path),
            config=AgentConfig(cwd=str(tmp_path)),
        )
        agent.run("go")
        child_id = _child_id_from_ack(next(
            m.content for m in agent.state.messages
            if isinstance(m, ToolResultMessage)
        ))
        gate.set()
        _wait_settled(agent, child_id)
        child = agent.subagent_continuable.get(child_id)
        assert child is not None
        report_acks = [
            m for m in child.messages
            if isinstance(m, ToolResultMessage)
            and "report accepted" in m.content
        ]
        assert len(report_acks) == 1
        assert re.search(r"as message \w+", report_acks[0].content)


class TestReportAuth:
    def test_one_shot_child_has_no_report_tool(self, tmp_path):
        llm = ThreadSafeScriptedLLM([
            AssistantMessage(content="", tool_calls=[
                ToolCall(
                    id="s1",
                    name="subagent",
                    arguments={"prompt": "try report"},
                ),
            ]),
            AssistantMessage(content="", tool_calls=[
                ToolCall(
                    id="r1",
                    name="report",
                    arguments={"output": "should fail"},
                ),
            ]),
            AssistantMessage(content="child noted missing tool"),
            AssistantMessage(content="parent done"),
        ])
        agent = Agent(
            llm=llm,
            tools=ToolRegistry([subagent_tool()]),
            policy=SandboxPolicy(root=tmp_path),
            config=AgentConfig(cwd=str(tmp_path)),
        )
        result = agent.run("delegate")
        assert result.output == "parent done"
        parent_tools = [
            m for m in agent.state.messages if isinstance(m, ToolResultMessage)
        ]
        assert "child noted missing tool" in parent_tools[0].content

    def test_report_without_sink_is_no_provider(self, tmp_path):
        result = ToolRegistry([report_tool()]).execute(
            "report",
            {"output": "hi"},
            ToolContext(policy=SandboxPolicy(root=tmp_path), extra={}),
        )
        assert result.is_error
        assert _code(result) == "NO_PROVIDER"

    def test_unauthorized_reporter(self, tmp_path):
        parent = Agent(
            llm=ThreadSafeScriptedLLM([]),
            tools=ToolRegistry([]),
            policy=SandboxPolicy(root=tmp_path),
            config=AgentConfig(cwd=str(tmp_path)),
        )
        stranger = Agent(
            llm=ThreadSafeScriptedLLM([]),
            tools=ToolRegistry([]),
            policy=SandboxPolicy(root=tmp_path),
            config=AgentConfig(cwd=str(tmp_path)),
        )
        from githi.subagent import SubagentError

        try:
            parent.subagent_continuable.report_from(stranger, "nope")
            assert False, "expected UNAUTHORIZED"
        except SubagentError as exc:
            assert exc.code == "UNAUTHORIZED"


class TestFormatReport:
    def test_frame(self):
        text = format_report_notice("abc123", "hello")
        assert text == "Background subagent abc123 reported:\nhello"


class TestDefaultTools:
    def test_report_not_on_parent_default_tools(self):
        assert "report" not in ToolRegistry(default_tools()).names()
