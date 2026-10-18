"""Continuable subagents: start, send_message, interrupt, settlement."""

from __future__ import annotations

import re
import threading
import time

from githi.agent.agent import Agent, AgentConfig
from githi.llm.client import Completion
from githi.sandbox.policy import SandboxPolicy
from githi.tools.base import Tool, ToolContext, ok
from githi.tools.interrupt_agent import interrupt_agent_tool
from githi.tools.registry import ToolRegistry, default_tools
from githi.tools.send_message import send_message_tool
from githi.tools.subagent import subagent_tool
from githi.tools.subagent_continuable import subagent_continuable_tool
from githi.types import (
    AssistantMessage,
    StopReason,
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


def _wait_notice(agent: Agent, child_id: str, timeout: float = 5.0) -> UserMessage:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if agent._follow_up.has_items():
            msg = agent._follow_up.drain()[0]
            assert isinstance(msg, UserMessage)
            if f"Background subagent {child_id}" in msg.content:
                return msg
        if agent._next_turn.has_items():
            msg = agent._next_turn.drain()[0]
            assert isinstance(msg, UserMessage)
            if f"Background subagent {child_id}" in msg.content:
                return msg
        time.sleep(0.01)
    raise AssertionError("settlement notice not delivered")


class TestContinuableStart:
    def test_ack_before_child_finishes(self, tmp_path):
        gate = threading.Event()
        llm = ThreadSafeScriptedLLM(
            [
                AssistantMessage(content="", tool_calls=[
                    ToolCall(
                        id="c1",
                        name="subagent_continuable",
                        arguments={"prompt": "research X"},
                    ),
                ]),
                AssistantMessage(content="parent done"),
                AssistantMessage(content="child closing"),
            ],
            child_gate=gate,
        )
        agent = Agent(
            llm=llm,
            tools=ToolRegistry([subagent_continuable_tool()]),
            policy=SandboxPolicy(root=tmp_path),
            config=AgentConfig(cwd=str(tmp_path)),
        )
        result = agent.run("start continuable")
        assert result.output == "parent done"
        ack = next(
            m for m in agent.state.messages if isinstance(m, ToolResultMessage)
        )
        assert ack.content.startswith("started subagent ")
        child_id = _child_id_from_ack(ack.content)
        child = agent.subagent_continuable.get(child_id)
        assert child is not None
        assert child.status == "running"
        gate.set()
        settled = _wait_settled(agent, child_id)
        assert settled.last_output == "child closing"
        notice = _wait_notice(agent, child_id)
        assert "will do no further work unless you send it more" in notice.content
        assert "child closing" in notice.content


class TestSendMessage:
    def test_send_after_settle_starts_new_epoch(self, tmp_path):
        gate = threading.Event()
        llm = ThreadSafeScriptedLLM(
            [
                AssistantMessage(content="", tool_calls=[
                    ToolCall(
                        id="c1",
                        name="subagent_continuable",
                        arguments={"prompt": "first"},
                    ),
                ]),
                AssistantMessage(content="parent after start"),
                AssistantMessage(content="first closing"),
                AssistantMessage(content="second closing"),
            ],
            child_gate=gate,
        )
        tools = ToolRegistry([
            subagent_continuable_tool(),
            send_message_tool(),
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
        gate.set()
        _wait_settled(agent, child_id)
        child = agent.subagent_continuable.get(child_id)
        assert child is not None
        assert child.status == "ready"
        assert child.agent is None
        assert child.messages  # transcript kept for cold resume
        first_notice = _wait_notice(agent, child_id)
        assert "first closing" in first_notice.content

        send = tools.execute(
            "send_message",
            {"subagent_id": child_id, "message": "do more"},
            ToolContext(
                policy=SandboxPolicy(root=tmp_path),
                extra={"subagent_continuable": agent.subagent_continuable},
            ),
        )
        assert not send.is_error
        assert "message queued" in send.text
        # Second epoch: child runs without gate (gate already set).
        settled = _wait_settled(agent, child_id)
        assert settled.last_output == "second closing"
        assert settled.agent is None
        notice = _wait_notice(agent, child_id)
        assert "second closing" in notice.content

    def test_send_during_settlement_is_not_lost(self, tmp_path):
        """send_message during settle/park window must become the next epoch."""
        gate = threading.Event()
        llm = ThreadSafeScriptedLLM(
            [
                AssistantMessage(content="", tool_calls=[
                    ToolCall(
                        id="c1",
                        name="subagent_continuable",
                        arguments={"prompt": "first"},
                    ),
                ]),
                AssistantMessage(content="parent after start"),
                AssistantMessage(content="first closing"),
                AssistantMessage(content="rescued closing"),
            ],
            child_gate=gate,
        )
        tools = ToolRegistry([
            subagent_continuable_tool(),
            send_message_tool(),
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

        registry = agent.subagent_continuable
        original_deliver = registry._deliver_settlement
        injected = threading.Event()

        def deliver_and_inject(child):
            # Mimic the race: parent sends while settlement is in flight and
            # status is still "running" with a live agent.
            if not injected.is_set():
                injected.set()
                registry.send_message(child_id, "rescue me")
            return original_deliver(child)

        registry._deliver_settlement = deliver_and_inject
        gate.set()
        settled = _wait_settled(agent, child_id, timeout=8.0)
        assert settled.last_output == "rescued closing"
        assert injected.is_set()


class TestInterrupt:
    def test_interrupt_stops_current_turn(self, tmp_path):
        gate = threading.Event()
        entered = threading.Event()

        def blocking_tool(args, ctx):
            entered.set()
            time.sleep(0.05)
            return ok("step")

        llm = ThreadSafeScriptedLLM(
            [
                AssistantMessage(content="", tool_calls=[
                    ToolCall(
                        id="c1",
                        name="subagent_continuable",
                        arguments={"prompt": "work"},
                    ),
                ]),
                AssistantMessage(content="parent done"),
                AssistantMessage(content="", tool_calls=[
                    ToolCall(id="b1", name="block", arguments={}),
                    ToolCall(id="b2", name="block", arguments={}),
                ]),
                AssistantMessage(content="should not finish"),
            ],
            child_gate=gate,
        )
        tools = ToolRegistry([
            subagent_continuable_tool(),
            interrupt_agent_tool(),
            Tool(
                name="block",
                description="block",
                parameters={"type": "object"},
                run=blocking_tool,
            ),
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
        gate.set()
        assert entered.wait(timeout=5)

        kill = tools.execute(
            "interrupt_agent",
            {"agent_id": child_id},
            ToolContext(
                policy=SandboxPolicy(root=tmp_path),
                extra={"subagent_continuable": agent.subagent_continuable},
            ),
        )
        assert not kill.is_error
        assert "interrupt requested" in kill.text
        settled = _wait_settled(agent, child_id)
        assert settled.last_stop_reason is StopReason.INTERRUPTED
        notice = _wait_notice(agent, child_id)
        assert "was stopped before it finished" in notice.content


class TestOneShotUnchanged:
    def test_foreground_subagent_still_awaits(self, tmp_path):
        llm = ThreadSafeScriptedLLM([
            AssistantMessage(content="", tool_calls=[
                ToolCall(
                    id="s1",
                    name="subagent",
                    arguments={"prompt": "summarize"},
                ),
            ]),
            AssistantMessage(content="child text"),
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
        tool_msgs = [
            m for m in agent.state.messages if isinstance(m, ToolResultMessage)
        ]
        assert tool_msgs[0].content == "child text"


class TestRegistryTools:
    def test_default_tools_include_continuable_controls(self):
        names = ToolRegistry(default_tools()).names()
        assert "subagent_continuable" in names
        assert "send_message" in names
        assert "interrupt_agent" in names

    def test_missing_registry(self, tmp_path):
        result = ToolRegistry([subagent_continuable_tool()]).execute(
            "subagent_continuable",
            {"prompt": "x"},
            ToolContext(policy=SandboxPolicy(root=tmp_path), extra={}),
        )
        assert result.is_error
        assert _code(result) == "NO_PROVIDER"
