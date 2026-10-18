"""Phase 3: settlement notices must wake the parent (promote + continue)."""

from __future__ import annotations

import threading
import time

from githi.agent.agent import Agent, AgentConfig
from githi.llm.client import Completion
from githi.sandbox.policy import SandboxPolicy
from githi.tools.registry import ToolRegistry
from githi.tools.subagent import subagent_tool
from githi.types import (
    AssistantMessage,
    ToolCall,
    ToolResultMessage,
    Usage,
    UserMessage,
)


class ThreadSafeScriptedLLM:
    def __init__(self, script, *, child_gate=None):
        self.script = list(script)
        self.calls = []
        self.model = "test-model"
        self._lock = threading.Lock()
        self._child_gate = child_gate

    def complete(self, messages, tools, **kw):
        is_child = threading.current_thread().name.startswith("githi-subagent")
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


def _job_id_from_ack(text: str) -> str:
    import re
    match = re.search(r"started background subagent job (\w+)", text)
    assert match, text
    return match.group(1)


def _wait_job(agent, job_id, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = agent.subagent_jobs.get(job_id)
        if job is not None and job.status != "running":
            return job
        time.sleep(0.01)
    raise AssertionError("job still running")


class TestSettlementWake:
    def test_undrained_next_turn_promoted_and_continued(self, tmp_path):
        """Leftover next_turn after a run is promoted and drained as follow-up."""
        llm = ThreadSafeScriptedLLM([
            AssistantMessage(content="parent finished early"),
            AssistantMessage(content="parent saw settlement"),
        ])
        agent = Agent(
            llm=llm,
            tools=ToolRegistry([]),
            policy=SandboxPolicy(root=tmp_path),
            config=AgentConfig(cwd=str(tmp_path)),
        )
        result = agent.run("go")
        assert result.output == "parent finished early"

        # Simulate a settlement that landed on next_turn after the final
        # no-tool answer (loop breaks before draining steering).
        agent._next_turn.enqueue(UserMessage(
            "Background subagent job abc finished (done).\n"
            "Its closing message:\nchild closing"
        ))
        result2 = agent.continue_queued()
        assert result2 is not None
        assert result2.output == "parent saw settlement"
        assert any(
            isinstance(m, UserMessage)
            and "Background subagent job abc finished" in m.content
            for m in agent.state.messages
        )

    def test_follow_up_after_run_drains_via_continue_queued(self, tmp_path):
        agent = Agent(
            llm=ThreadSafeScriptedLLM([
                AssistantMessage(content="first"),
                AssistantMessage(content="after notice"),
            ]),
            tools=ToolRegistry([]),
            policy=SandboxPolicy(root=tmp_path),
            config=AgentConfig(cwd=str(tmp_path)),
        )
        result = agent.run("go")
        assert result.output == "first"
        agent.follow_up(UserMessage(
            "Background subagent job x finished (done).\n"
            "Its closing message:\nok"
        ))
        result2 = agent.continue_queued()
        assert result2 is not None
        assert result2.output == "after notice"

    def test_run_drains_follow_up_already_queued(self, tmp_path):
        llm = ThreadSafeScriptedLLM([
            AssistantMessage(content="first"),
            AssistantMessage(content="second"),
        ])
        agent = Agent(
            llm=llm,
            tools=ToolRegistry([]),
            policy=SandboxPolicy(root=tmp_path),
            config=AgentConfig(cwd=str(tmp_path)),
        )
        agent.follow_up(UserMessage("one more"))
        result = agent.run("go")
        assert result.output == "second"
        assert len(llm.calls) == 2
