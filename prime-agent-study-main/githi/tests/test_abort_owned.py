"""Phase 5: ESC / abort_owned cancels background jobs and continuables."""

from __future__ import annotations

import threading
import time

from githi.agent.agent import Agent, AgentConfig
from githi.llm.client import Completion
from githi.sandbox.policy import SandboxPolicy
from githi.tools.base import Tool, ok
from githi.tools.registry import ToolRegistry
from githi.tools.subagent import subagent_tool
from githi.tools.subagent_continuable import subagent_continuable_tool
from githi.types import AssistantMessage, StopReason, ToolCall, Usage


class ThreadSafeScriptedLLM:
    def __init__(self, script, *, child_gate=None):
        self.script = list(script)
        self.calls = []
        self.model = "test-model"
        self._lock = threading.Lock()
        self._child_gate = child_gate

    def complete(self, messages, tools, **kw):
        name = threading.current_thread().name
        is_child = name.startswith("githi-subagent") or name.startswith(
            "githi-continuable"
        )
        if is_child and self._child_gate is not None:
            assert self._child_gate.wait(timeout=5)
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


def _wait_job(agent, job_id, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = agent.subagent_jobs.get(job_id)
        if job is not None and job.status != "running":
            return job
        time.sleep(0.01)
    raise AssertionError("job still running")


class TestAbortOwnedBackground:
    def test_abort_cancels_background_job(self, tmp_path):
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
                        id="s1",
                        name="subagent",
                        arguments={
                            "prompt": "bg",
                            "run_in_background": True,
                        },
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
            subagent_tool(),
            Tool(
                name="block",
                description="d",
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
        import re
        ack = next(
            m for m in agent.state.messages
            if getattr(m, "content", "").startswith("started background")
        )
        job_id = re.search(r"job (\w+)", ack.content).group(1)
        gate.set()
        assert entered.wait(timeout=5)
        assert agent.abort_owned_background()
        job = _wait_job(agent, job_id)
        assert job.status == "cancelled"
        assert job.result is not None
        assert job.result.stop_reason is StopReason.INTERRUPTED

    def test_abort_interrupts_running_continuable(self, tmp_path):
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
            Tool(
                name="block",
                description="d",
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
        children = agent.subagent_continuable.list_children()
        assert children
        child_id = children[0]["id"]
        gate.set()
        assert entered.wait(timeout=5)
        assert agent.abort_owned_background()
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            child = agent.subagent_continuable.get(child_id)
            if child and child.status == "ready":
                assert child.last_stop_reason is StopReason.INTERRUPTED
                return
            time.sleep(0.01)
        raise AssertionError("continuable did not settle interrupted")
