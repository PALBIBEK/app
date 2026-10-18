"""The stateful `Agent` wrapper (port of `agent.ts`).

The loop is stateless — it takes a context and runs turns. `Agent` owns what
persists *between* runs: the transcript, listeners, and the two message
queues that let a human intervene while work is in flight.

The queues are the substantive part. Without them the only way to redirect a
running agent is to kill it and start over, losing everything it had done.

* **steering** — drained *during* a run, before the next assistant turn:
  "actually, use the other API"
* **follow-up** — drained *after* the agent would have stopped, restarting it:
  "now also update the docs"

`QueueMode` matters because draining five queued corrections at once presents
them as simultaneous and contradictory, rather than as a sequence.
"""

from __future__ import annotations

import pytest

from githi.agent.agent import Agent, AgentConfig, PendingMessageQueue
from githi.agent.events import Event, EventType
from githi.llm.client import Completion
from githi.sandbox.policy import SandboxPolicy
from githi.tools.base import Tool, ToolResult
from githi.tools.registry import ToolRegistry
from githi.types import AssistantMessage, QueueMode, ToolCall, Usage, UserMessage


class ScriptedLLM:
    def __init__(self, script):
        self.script = list(script)
        self.calls: list[list] = []
        self.model = "test-model"

    def complete(self, messages, tools, **kw):
        self.calls.append(list(messages))
        message = self.script.pop(0) if self.script else AssistantMessage(content="done")
        return Completion(message=message, usage=Usage(input_tokens=1))


def _tool(name="bash"):
    return Tool(name=name, description="d", parameters={"type": "object"},
                run=lambda a, c: ToolResult(content="ran"))


def _agent(script, tmp_path, **cfg):
    return Agent(llm=ScriptedLLM(script), tools=ToolRegistry([_tool()]),
                 policy=SandboxPolicy(root=tmp_path), config=AgentConfig(**cfg))


class TestPendingMessageQueue:
    def test_starts_empty(self):
        assert not PendingMessageQueue(QueueMode.ALL).has_items()

    def test_enqueue_then_drain_all(self):
        q = PendingMessageQueue(QueueMode.ALL)
        q.enqueue(UserMessage("a"))
        q.enqueue(UserMessage("b"))
        assert [m.content for m in q.drain()] == ["a", "b"]
        assert not q.has_items()

    def test_one_at_a_time_drains_the_oldest_only(self):
        """Draining every correction at once presents them as simultaneous
        and contradictory instead of as a sequence."""
        q = PendingMessageQueue(QueueMode.ONE_AT_A_TIME)
        q.enqueue(UserMessage("first"))
        q.enqueue(UserMessage("second"))
        assert [m.content for m in q.drain()] == ["first"]
        assert q.has_items(), "the rest must stay queued"
        assert [m.content for m in q.drain()] == ["second"]

    def test_draining_an_empty_queue_is_harmless(self):
        assert PendingMessageQueue(QueueMode.ONE_AT_A_TIME).drain() == []

    def test_clear_discards_everything(self):
        q = PendingMessageQueue(QueueMode.ALL)
        q.enqueue(UserMessage("a"))
        q.clear()
        assert not q.has_items()

    def test_drain_returns_a_copy(self):
        """A caller mutating the drained list must not corrupt the queue."""
        q = PendingMessageQueue(QueueMode.ALL)
        q.enqueue(UserMessage("a"))
        drained = q.drain()
        drained.append(UserMessage("injected"))
        assert not q.has_items()

    def test_mode_is_changeable_at_runtime(self):
        q = PendingMessageQueue(QueueMode.ALL)
        q.mode = QueueMode.ONE_AT_A_TIME
        q.enqueue(UserMessage("a"))
        q.enqueue(UserMessage("b"))
        assert len(q.drain()) == 1


class TestSteering:
    def test_steer_reaches_the_next_turn(self, tmp_path):
        agent = _agent([AssistantMessage(content="", tool_calls=[ToolCall("c1", "bash")]),
                        AssistantMessage(content="done")], tmp_path)
        agent.steer(UserMessage("use the other API"))
        agent.run("go")
        assert any(getattr(m, "content", "") == "use the other API"
                   for m in agent.llm.calls[0])

    def test_steering_queue_is_consumed(self, tmp_path):
        agent = _agent([AssistantMessage(content="done")], tmp_path)
        agent.steer(UserMessage("x"))
        agent.run("go")
        assert not agent.has_queued_messages()

    def test_clear_steering_queue(self, tmp_path):
        agent = _agent([AssistantMessage(content="done")], tmp_path)
        agent.steer(UserMessage("x"))
        agent.clear_steering_queue()
        assert not agent.has_queued_messages()


class TestFollowUp:
    def test_follow_up_restarts_a_finished_run(self, tmp_path):
        """The agent answered and would have exited; a queued follow-up
        continues it instead of requiring a fresh run."""
        agent = _agent([AssistantMessage(content="first"),
                        AssistantMessage(content="second")], tmp_path)
        agent.follow_up(UserMessage("one more thing"))
        agent.run("go")
        assert len(agent.llm.calls) == 2

    def test_no_follow_up_means_one_pass(self, tmp_path):
        agent = _agent([AssistantMessage(content="done")], tmp_path)
        agent.run("go")
        assert len(agent.llm.calls) == 1

    def test_clear_all_queues(self, tmp_path):
        agent = _agent([AssistantMessage(content="done")], tmp_path)
        agent.steer(UserMessage("a"))
        agent.follow_up(UserMessage("b"))
        agent.clear_all_queues()
        assert not agent.has_queued_messages()


class TestListeners:
    def test_subscribe_receives_events(self, tmp_path):
        agent = _agent([AssistantMessage(content="done")], tmp_path)
        seen: list[Event] = []
        agent.subscribe(seen.append)
        agent.run("go")
        assert any(e.type is EventType.TURN_START for e in seen)

    def test_unsubscribe_stops_delivery(self, tmp_path):
        """pi returns an unsubscribe closure; a listener that cannot be
        removed leaks for the life of the agent."""
        agent = _agent([AssistantMessage(content="done")], tmp_path)
        seen: list[Event] = []
        unsubscribe = agent.subscribe(seen.append)
        unsubscribe()
        agent.run("go")
        assert seen == []

    def test_a_failing_listener_does_not_break_the_run(self, tmp_path):
        agent = _agent([AssistantMessage(content="done")], tmp_path)
        agent.subscribe(lambda e: (_ for _ in ()).throw(RuntimeError("bad listener")))
        seen: list[Event] = []
        agent.subscribe(seen.append)
        assert agent.run("go").output == "done"
        assert seen


class TestStateAndReset:
    def test_transcript_persists_across_runs(self, tmp_path):
        """`Agent` owns the transcript -- a second run continues the
        conversation rather than starting a new one."""
        agent = _agent([AssistantMessage(content="first"),
                        AssistantMessage(content="second")], tmp_path)
        agent.run("hello")
        agent.run("again")
        assert any(getattr(m, "content", "") == "first" for m in agent.llm.calls[1])

    def test_reset_clears_the_transcript(self, tmp_path):
        agent = _agent([AssistantMessage(content="first"),
                        AssistantMessage(content="second")], tmp_path)
        agent.run("hello")
        agent.reset()
        agent.run("again")
        assert not any(getattr(m, "content", "") == "first" for m in agent.llm.calls[1])

    def test_reset_clears_the_queues(self, tmp_path):
        agent = _agent([AssistantMessage(content="done")], tmp_path)
        agent.steer(UserMessage("x"))
        agent.reset()
        assert not agent.has_queued_messages()

    def test_queue_modes_are_configurable(self, tmp_path):
        agent = _agent([AssistantMessage(content="done")], tmp_path,
                       steering_mode=QueueMode.ONE_AT_A_TIME)
        assert agent.steering_mode is QueueMode.ONE_AT_A_TIME
