"""Background one-shot subagent jobs + settlement notices."""

from __future__ import annotations

import re
import threading
import time

from githi.agent.agent import Agent, AgentConfig
from githi.llm.client import Completion
from githi.sandbox.policy import SandboxPolicy
from githi.subagent.jobs import format_settlement_notice
from githi.tools.base import Tool, ToolContext, ok
from githi.tools.job_kill import job_kill_tool
from githi.tools.job_output import job_output_tool
from githi.tools.registry import ToolRegistry, default_tools
from githi.tools.subagent import subagent_tool
from githi.tools.subagent_fork import subagent_fork_tool
from githi.types import (
    AssistantMessage,
    StopReason,
    ToolCall,
    ToolResultMessage,
    Usage,
    UserMessage,
)


class ThreadSafeScriptedLLM:
    """Scripted complete() safe for parent + background child threads."""

    def __init__(self, script, *, child_gate: threading.Event | None = None):
        self.script = list(script)
        self.calls: list[list] = []
        self.model = "test-model"
        self._lock = threading.Lock()
        self._child_gate = child_gate

    def complete(self, messages, tools, **kw):
        is_child = threading.current_thread().name.startswith("githi-subagent")
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


def _job_id_from_ack(text: str) -> str:
    match = re.search(r"started background subagent job (\w+)", text)
    assert match, text
    return match.group(1)


def _wait_job(agent: Agent, job_id: str, timeout: float = 5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = agent.subagent_jobs.get(job_id)
        if job is not None and job.status != "running":
            return job
        time.sleep(0.01)
    raise AssertionError(f"job {job_id} still running after {timeout}s")


class TestBackgroundAck:
    def test_ack_returns_before_child_finishes(self, tmp_path):
        gate = threading.Event()
        llm = ThreadSafeScriptedLLM(
            [
                AssistantMessage(content="", tool_calls=[
                    ToolCall(
                        id="s1",
                        name="subagent",
                        arguments={
                            "prompt": "slow work",
                            "run_in_background": True,
                        },
                    ),
                ]),
                AssistantMessage(content="parent done without waiting"),
                AssistantMessage(content="child answer"),
            ],
            child_gate=gate,
        )
        tools = ToolRegistry([subagent_tool()])
        agent = Agent(
            llm=llm,
            tools=tools,
            policy=SandboxPolicy(root=tmp_path),
            config=AgentConfig(cwd=str(tmp_path)),
        )
        result = agent.run("delegate in background")
        assert result.stop_reason is StopReason.DONE
        assert result.output == "parent done without waiting"
        tool_msgs = [
            m for m in agent.state.messages if isinstance(m, ToolResultMessage)
        ]
        assert len(tool_msgs) == 1
        assert tool_msgs[0].content.startswith("started background subagent job ")
        job_id = _job_id_from_ack(tool_msgs[0].content)
        job = agent.subagent_jobs.get(job_id)
        assert job is not None
        assert job.status == "running"
        gate.set()
        finished = _wait_job(agent, job_id)
        assert finished.status == "completed"
        assert finished.result is not None
        assert finished.result.output == "child answer"


class TestSettlementDelivery:
    def test_idle_uses_follow_up(self, tmp_path):
        gate = threading.Event()
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
                AssistantMessage(content="parent finished"),
                AssistantMessage(content="child closing"),
            ],
            child_gate=gate,
        )
        agent = Agent(
            llm=llm,
            tools=ToolRegistry([subagent_tool()]),
            policy=SandboxPolicy(root=tmp_path),
            config=AgentConfig(cwd=str(tmp_path)),
        )
        agent.run("go")
        assert not agent._run_active
        ack = next(
            m for m in agent.state.messages if isinstance(m, ToolResultMessage)
        )
        job_id = _job_id_from_ack(ack.content)
        gate.set()
        _wait_job(agent, job_id)
        # Give settlement a moment to enqueue after status flip.
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline and not agent._follow_up.has_items():
            time.sleep(0.01)
        assert agent._follow_up.has_items()
        assert not agent._next_turn.has_items()
        notice = agent._follow_up.drain()[0]
        assert isinstance(notice, UserMessage)
        assert f"Background subagent job {job_id} finished" in notice.content
        assert "child closing" in notice.content

    def test_mid_run_uses_next_turn(self, tmp_path):
        gate = threading.Event()

        def await_and_capture(args, ctx):
            ack = next(
                m for m in agent.state.messages
                if isinstance(m, ToolResultMessage)
                and "started background subagent job" in m.content
            )
            job_id = _job_id_from_ack(ack.content)
            gate.set()
            _wait_job(agent, job_id)
            assert agent._run_active
            assert agent._next_turn.has_items()
            return ok("waited")

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
                AssistantMessage(content="", tool_calls=[
                    ToolCall(id="w1", name="await_job", arguments={}),
                ]),
                # Child runs while await_job is blocked, so its reply must be
                # next in the shared script before the parent's final turn.
                AssistantMessage(content="child closing"),
                AssistantMessage(content="parent after settlement"),
            ],
            child_gate=gate,
        )
        tools = ToolRegistry([
            subagent_tool(),
            Tool(
                name="await_job",
                description="wait",
                parameters={"type": "object"},
                run=await_and_capture,
            ),
        ])
        agent = Agent(
            llm=llm,
            tools=tools,
            policy=SandboxPolicy(root=tmp_path),
            config=AgentConfig(cwd=str(tmp_path)),
        )
        result = agent.run("go")
        assert result.stop_reason is StopReason.DONE
        assert result.output == "parent after settlement"
        notices = [
            m for m in agent.state.messages
            if isinstance(m, UserMessage)
            and "Background subagent job" in m.content
        ]
        assert len(notices) == 1
        assert "child closing" in notices[0].content


class TestJobOutputAndKill:
    def test_job_output_after_complete(self, tmp_path):
        from githi.tools.base import ToolContext

        gate = threading.Event()
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
                AssistantMessage(content="recovered closing"),
            ],
            child_gate=gate,
        )
        tools = ToolRegistry([subagent_tool(), job_output_tool()])
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
        job_id = _job_id_from_ack(ack.content)
        gate.set()
        _wait_job(agent, job_id)

        result = tools.execute(
            "job_output",
            {"job_id": job_id},
            ToolContext(
                policy=SandboxPolicy(root=tmp_path),
                extra={"subagent_jobs": agent.subagent_jobs},
            ),
        )
        assert not result.is_error
        assert "recovered closing" in result.text
        assert result.details["status"] == "completed"

    def test_job_kill_cancels_running_child(self, tmp_path):
        gate = threading.Event()
        entered = threading.Event()

        def blocking_tool(args, ctx):
            entered.set()
            # Stay until interrupted via per-job Interrupt polling in the loop
            # between tools — return then check interrupt on next tool.
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
                # Child: first tool batch, then would continue if not killed.
                AssistantMessage(content="", tool_calls=[
                    ToolCall(id="b1", name="block", arguments={}),
                    ToolCall(id="b2", name="block", arguments={}),
                ]),
                AssistantMessage(content="child should not finish cleanly"),
            ],
            child_gate=gate,
        )
        tools = ToolRegistry([
            subagent_tool(),
            job_kill_tool(),
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
        job_id = _job_id_from_ack(ack.content)
        gate.set()
        assert entered.wait(timeout=5)

        from githi.tools.base import ToolContext

        kill_result = tools.execute(
            "job_kill",
            {"job_id": job_id},
            ToolContext(
                policy=SandboxPolicy(root=tmp_path),
                extra={"subagent_jobs": agent.subagent_jobs},
            ),
        )
        assert not kill_result.is_error
        assert "sent cancel" in kill_result.text
        job = _wait_job(agent, job_id)
        assert job.status == "cancelled"

    def test_job_output_unknown_id(self, tmp_path):
        from githi.tools.base import ToolContext

        agent = Agent(
            llm=ThreadSafeScriptedLLM([]),
            tools=ToolRegistry([job_output_tool()]),
            policy=SandboxPolicy(root=tmp_path),
            config=AgentConfig(cwd=str(tmp_path)),
        )
        result = agent.tools.execute(
            "job_output",
            {"job_id": "missing"},
            ToolContext(
                policy=SandboxPolicy(root=tmp_path),
                extra={"subagent_jobs": agent.subagent_jobs},
            ),
        )
        assert result.is_error
        assert _code(result) == "NOT_FOUND"


class TestForegroundUnchanged:
    def test_foreground_still_awaits_child(self, tmp_path):
        llm = ThreadSafeScriptedLLM([
            AssistantMessage(content="", tool_calls=[
                ToolCall(
                    id="s1",
                    name="subagent",
                    arguments={"prompt": "summarize X"},
                ),
            ]),
            AssistantMessage(content="child summary of X"),
            AssistantMessage(content="parent done"),
        ])
        agent = Agent(
            llm=llm,
            tools=ToolRegistry([subagent_tool()]),
            policy=SandboxPolicy(root=tmp_path),
            config=AgentConfig(cwd=str(tmp_path)),
        )
        result = agent.run("please delegate")
        assert result.output == "parent done"
        tool_msgs = [
            m for m in agent.state.messages if isinstance(m, ToolResultMessage)
        ]
        assert tool_msgs[0].content == "child summary of X"


class TestBackgroundFork:
    def test_fork_background_ack(self, tmp_path):
        gate = threading.Event()
        llm = ThreadSafeScriptedLLM(
            [
                AssistantMessage(content="", tool_calls=[
                    ToolCall(
                        id="s1",
                        name="subagent_fork",
                        arguments={
                            "prompt": "continue",
                            "run_in_background": True,
                        },
                    ),
                ]),
                AssistantMessage(content="parent done"),
                AssistantMessage(content="fork child done"),
            ],
            child_gate=gate,
        )
        agent = Agent(
            llm=llm,
            tools=ToolRegistry([subagent_fork_tool()]),
            policy=SandboxPolicy(root=tmp_path),
            config=AgentConfig(cwd=str(tmp_path)),
        )
        agent.run("fork bg")
        ack = next(
            m for m in agent.state.messages if isinstance(m, ToolResultMessage)
        )
        assert "started background subagent job" in ack.content
        job_id = _job_id_from_ack(ack.content)
        gate.set()
        job = _wait_job(agent, job_id)
        assert job.mode == "fork"
        assert job.result.output == "fork child done"

    def test_fork_background_seed_taken_at_ack_not_later(self, tmp_path):
        """Seed must be the completed-turn prefix at job start, not after parent continues."""
        from githi.types import SystemMessage

        gate = threading.Event()
        worker_hold = threading.Event()
        parent_finished = threading.Event()

        llm = ThreadSafeScriptedLLM(
            [
                AssistantMessage(content="", tool_calls=[
                    ToolCall(
                        id="s1",
                        name="subagent_fork",
                        arguments={
                            "prompt": "use prior choice",
                            "run_in_background": True,
                        },
                    ),
                ]),
                AssistantMessage(content="parent done after ack"),
                AssistantMessage(content="fork child done"),
            ],
            child_gate=gate,
        )
        agent = Agent(
            llm=llm,
            tools=ToolRegistry([subagent_fork_tool()]),
            policy=SandboxPolicy(root=tmp_path),
            config=AgentConfig(cwd=str(tmp_path)),
        )
        agent.state.messages[:] = [
            SystemMessage("sys"),
            UserMessage("we prefer pytest"),
            AssistantMessage(content="agreed on pytest"),
        ]

        # Force the bug window: delay the worker until the parent has finished
        # its run (tool ack closed + final assistant appended).
        original_run_job = agent.subagent_jobs._run_job

        def delayed_run_job(job, prompt, parent_depth):
            worker_hold.set()
            assert parent_finished.wait(timeout=5)
            return original_run_job(job, prompt, parent_depth)

        agent.subagent_jobs._run_job = delayed_run_job

        def run_parent():
            agent.run("please fork in background")
            parent_finished.set()

        parent_thread = threading.Thread(target=run_parent)
        parent_thread.start()
        assert worker_hold.wait(timeout=5)
        # Parent may still be finishing; wait until it signals done.
        assert parent_finished.wait(timeout=5)
        parent_thread.join(timeout=5)

        ack = next(
            m for m in agent.state.messages if isinstance(m, ToolResultMessage)
        )
        job_id = _job_id_from_ack(ack.content)
        gate.set()
        _wait_job(agent, job_id)

        child_calls = [
            msgs for msgs in llm.calls
            if any(
                isinstance(m, UserMessage) and "use prior choice" in str(m.content)
                for m in msgs
            )
        ]
        assert child_calls, "expected child LLM call with fork prompt"
        child_msgs = child_calls[0]
        texts = [getattr(m, "content", "") for m in child_msgs]
        assert any("we prefer pytest" in str(t) for t in texts)
        assert any("agreed on pytest" in str(t) for t in texts)
        # Must not include parent work that happened after the ack.
        assert not any("parent done after ack" in str(t) for t in texts)
        assert not any(
            isinstance(m, ToolResultMessage)
            and "started background subagent job" in m.content
            for m in child_msgs
        )
        assert not any(
            isinstance(m, AssistantMessage)
            and m.tool_calls
            and any(c.name == "subagent_fork" for c in m.tool_calls)
            for m in child_msgs
        )


class TestDefaultTools:
    def test_registry_lists_job_tools(self):
        names = ToolRegistry(default_tools()).names()
        assert "job_output" in names
        assert "job_kill" in names
        assert "subagent" in names


class TestFormatNotice:
    def test_empty_closing(self):
        from githi.subagent import SubagentRunResult
        from githi.subagent.jobs import SubagentJob

        job = SubagentJob(
            id="abc",
            mode="spawn",
            status="completed",
            result=SubagentRunResult(
                id="abc",
                output="  ",
                stop_reason=StopReason.DONE,
            ),
        )
        text = format_settlement_notice(job)
        assert "It left no closing message." in text
