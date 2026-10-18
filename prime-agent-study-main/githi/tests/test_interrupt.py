"""ESC while the agent is working, then type a correction (pi / Claude Code).

The mechanism is a `threading.Event` the agent polls, set by a reader thread
watching stdin in raw mode. The agent core stays synchronous -- no asyncio,
no rewrite of the 849 tests that already pass.

**The invariant an interrupt threatens.** Stopping mid-turn is easy; stopping
without corrupting the transcript is not. If the model has emitted an
assistant message with three tool calls and the user hits ESC after the
first, the transcript holds three `tool_calls` and one result. Every
OpenAI-compatible provider rejects that with a 400 -- so the next thing the
user types would fail, and the error would name neither the interrupt nor the
missing results. Synthetic "interrupted" results for the un-executed calls
are therefore not politeness, they are what keeps the session usable.

**What the model is told matters too.** An interrupted tool must not read as
a *failed* tool. A failure invites a retry, which is the opposite of what the
user asked for by pressing ESC.

Raw-mode terminal handling is guarded carefully: it must degrade when stdin
is not a TTY (pipes, CI, this test suite), and it must restore the terminal
even when the body raises -- a crash that leaves the terminal in raw mode
makes the user's shell unusable afterwards.
"""

from __future__ import annotations

import io
import threading

import pytest

from githi.agent.agent import Agent, AgentConfig
from githi.agent.interrupt import ESC, Interrupt, KeyReader
from githi.llm.client import Completion
from githi.sandbox.policy import SandboxPolicy
from githi.tools.base import Tool, ToolResult
from githi.tools.registry import ToolRegistry
from githi.types import AssistantMessage, StopReason, ToolCall, ToolResultMessage, Usage


class TestInterruptFlag:
    def test_starts_clear(self):
        assert not Interrupt().is_set()

    def test_set_and_clear(self):
        flag = Interrupt()
        flag.set()
        assert flag.is_set()
        flag.clear()
        assert not flag.is_set()

    def test_it_is_thread_safe(self):
        """Set from one thread, observed from another -- the entire point."""
        flag = Interrupt()
        threading.Thread(target=flag.set).start()
        flag.wait(timeout=2)
        assert flag.is_set()

    def test_counts_repeated_interrupts(self):
        """Two ESCs in quick succession mean 'I really mean it'; a caller may
        use the count to escalate from stop-gracefully to exit."""
        flag = Interrupt()
        flag.set()
        flag.set()
        assert flag.count == 2

    def test_clear_resets_the_count(self):
        flag = Interrupt()
        flag.set()
        flag.clear()
        assert flag.count == 0


class TestKeyReader:
    def test_a_non_tty_degrades_quietly(self):
        """Piped stdin has no raw mode. It must not raise -- the same binary
        runs in CI and under `githi < script.txt`."""
        with KeyReader(stream=io.StringIO("x"), interrupt=Interrupt()) as reader:
            assert not reader.active

    def test_the_terminal_is_restored_when_the_body_raises(self):
        """A crash that leaves the terminal in raw mode makes the user's
        shell unusable long after githi exits."""
        restored = []

        class FakeReader(KeyReader):
            def _restore(self):
                restored.append(True)

        with pytest.raises(RuntimeError):
            with FakeReader(stream=io.StringIO(), interrupt=Interrupt()):
                raise RuntimeError("boom")
        assert restored

    def test_esc_sets_the_flag(self):
        flag = Interrupt()
        reader = KeyReader(stream=io.StringIO(), interrupt=flag)
        reader._handle(ESC)
        assert flag.is_set()

    def test_other_keys_do_not(self):
        flag = Interrupt()
        reader = KeyReader(stream=io.StringIO(), interrupt=flag)
        reader._handle("a")
        assert not flag.is_set()

    def test_key_handler_can_consume_escape(self):
        """The in-flight input collector needs ESC+Enter for follow-up input."""
        flag = Interrupt()
        reader = KeyReader(stream=io.StringIO(), interrupt=flag,
                           on_key=lambda key: key == ESC)
        reader._handle(ESC)
        assert not flag.is_set()

    def test_pause_is_a_no_op_when_not_active(self):
        """Pipes / CI never enter raw mode; pause must not raise."""
        reader = KeyReader(stream=io.StringIO(), interrupt=Interrupt())
        reader.pause()
        assert not reader.active

    def test_resume_is_a_no_op_when_already_active(self):
        """Double-resume must not start a second reader thread."""
        started = []

        class FakeReader(KeyReader):
            def _enter_raw_mode(self):
                started.append(1)
                return True

        reader = FakeReader(stream=io.StringIO(), interrupt=Interrupt())
        reader.active = True
        reader.resume()
        assert started == []

    def test_pause_then_resume_restores_watching(self):
        """ask_user_question: pause for cooked input, then watch ESC again."""
        events: list[str] = []

        class FakeReader(KeyReader):
            def _enter_raw_mode(self):
                events.append("enter")
                return True

            def _restore(self):
                events.append("restore")
                self._saved = None

        reader = FakeReader(stream=io.StringIO(), interrupt=Interrupt())
        with reader:
            assert reader.active
            assert events == ["enter"]
            reader.pause()
            assert not reader.active
            assert events == ["enter", "restore"]
            reader.resume()
            assert reader.active
            assert events == ["enter", "restore", "enter"]
        assert not reader.active
        assert events[-1] == "restore"


class ScriptedLLM:
    def __init__(self, script):
        self.script = list(script)
        self.calls: list[list] = []
        self.model = "test-model"

    def complete(self, messages, tools, **kw):
        self.calls.append(list(messages))
        message = self.script.pop(0) if self.script else AssistantMessage(content="done")
        return Completion(message=message, usage=Usage(input_tokens=1))


def _three_calls():
    return AssistantMessage(content="", tool_calls=[
        ToolCall("c1", "bash", {}), ToolCall("c2", "bash", {}),
        ToolCall("c3", "bash", {})])


class TestInterruptDuringTools:
    def _agent(self, tmp_path, flag, on_run=None):
        def run(args, ctx):
            if on_run:
                on_run()
            return ToolResult(content="ran")

        return Agent(llm=ScriptedLLM([_three_calls(),
                                      AssistantMessage(content="done")]),
                     tools=ToolRegistry([Tool(name="bash", description="d",
                                              parameters={"type": "object"},
                                              run=run)]),
                     policy=SandboxPolicy(root=tmp_path),
                     config=AgentConfig(interrupt=flag))

    def test_remaining_tools_do_not_execute(self, tmp_path):
        flag = Interrupt()
        executed = []
        agent = self._agent(tmp_path, flag,
                            on_run=lambda: (executed.append(1), flag.set()))
        agent.run("go")
        assert len(executed) == 1, "tools kept running after the interrupt"

    def test_every_tool_call_still_gets_a_result(self, tmp_path):
        """The wire-format invariant. Three tool_calls and one result is a
        400 from the provider on the *next* request."""
        flag = Interrupt()
        agent = self._agent(tmp_path, flag, on_run=flag.set)
        agent.run("go")
        results = [m for m in agent.state.messages
                   if isinstance(m, ToolResultMessage)]
        assert {r.tool_call_id for r in results} == {"c1", "c2", "c3"}

    def test_the_synthetic_results_say_interrupted_not_failed(self, tmp_path):
        """A failure invites a retry -- exactly what ESC was pressed to
        prevent."""
        flag = Interrupt()
        agent = self._agent(tmp_path, flag, on_run=flag.set)
        agent.run("go")
        synthetic = [m for m in agent.state.messages
                     if isinstance(m, ToolResultMessage) and m.tool_call_id == "c3"]
        assert "interrupt" in synthetic[0].content.lower()

    def test_the_run_stops(self, tmp_path):
        flag = Interrupt()
        agent = self._agent(tmp_path, flag, on_run=flag.set)
        assert agent.run("go").stop_reason is StopReason.INTERRUPTED

    def test_work_done_before_the_interrupt_is_kept(self, tmp_path):
        """The user paid for it, and it is what they will build on next."""
        flag = Interrupt()
        agent = self._agent(tmp_path, flag, on_run=flag.set)
        agent.run("go")
        real = [m for m in agent.state.messages
                if isinstance(m, ToolResultMessage) and m.content == "ran"]
        assert len(real) == 1


class TestResumeAfterInterrupt:
    def _agent(self, tmp_path, flag):
        return Agent(llm=ScriptedLLM([_three_calls(),
                                      AssistantMessage(content="first"),
                                      AssistantMessage(content="second")]),
                     tools=ToolRegistry([Tool(name="bash", description="d",
                                              parameters={"type": "object"},
                                              run=lambda a, c: (flag.set(),
                                                                ToolResult(content="ran"))[1])]),
                     policy=SandboxPolicy(root=tmp_path),
                     config=AgentConfig(interrupt=flag))

    def test_a_second_run_continues_the_conversation(self, tmp_path):
        """What the user does after ESC: type a correction and carry on."""
        flag = Interrupt()
        agent = self._agent(tmp_path, flag)
        agent.run("go")
        flag.clear()
        result = agent.run("actually do it differently")
        assert result.stop_reason is not StopReason.INTERRUPTED

    def test_the_new_message_reaches_the_model(self, tmp_path):
        flag = Interrupt()
        agent = self._agent(tmp_path, flag)
        agent.run("go")
        flag.clear()
        agent.run("actually do it differently")
        assert any("actually do it differently" in (getattr(m, "content", "") or "")
                   for m in agent.llm.calls[-1])

    def test_the_interrupted_context_is_still_there(self, tmp_path):
        """Resuming must not mean starting over -- the point of interrupting
        rather than killing the process."""
        flag = Interrupt()
        agent = self._agent(tmp_path, flag)
        agent.run("go")
        flag.clear()
        agent.run("carry on")
        assert any(isinstance(m, ToolResultMessage) for m in agent.llm.calls[-1])

    def test_a_stale_flag_does_not_abort_the_next_run(self, tmp_path):
        """Forgetting to clear would make every later run stop instantly, and
        the cause would be invisible."""
        flag = Interrupt()
        agent = self._agent(tmp_path, flag)
        agent.run("go")
        assert flag.is_set()
        flag.clear()
        assert agent.run("again").stop_reason is not StopReason.INTERRUPTED
