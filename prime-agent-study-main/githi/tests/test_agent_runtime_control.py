"""Runtime control (port of `harness/agent-harness.ts`).

pi's `AgentHarness` can be reconfigured *while it holds a live conversation*.
githi could only be configured at construction, which forces a caller who
wants a bigger model for one hard turn to build a second agent -- and a second
agent starts with an empty transcript, so it does not know what the first one
found.

The pieces, and what each is actually for:

* **`next_turn`** -- a third queue, distinct from the two githi already has.
  Steering interrupts work in flight; follow-up restarts work that finished;
  `next_turn` injects context at the start of the following turn without
  implying either. This is where a memory recall or a lint result belongs.
* **`append_message`** -- put something in the transcript without running a
  turn. Seeding known context should not cost an LLM call.
* **`set_model` / `set_thinking_level`** -- escalate for the hard part and
  drop back down, on one continuous transcript.
* **`set_active_tools`** -- narrow the offered tools without losing the
  registry. Fewer tools is a real accuracy lever: the model cannot misuse a
  tool it was not offered. Distinct from removing them, because a phase
  machine has to be able to re-offer them later.
* **`compact_now`** -- compact on demand rather than only at the threshold.

`set_active_tools` validates against the registry deliberately: a typo'd name
would otherwise silently narrow the tool set to nothing, and the failure would
surface much later as the model inexplicably refusing to act.
"""

from __future__ import annotations

import pytest

from githi.agent.agent import Agent, AgentConfig
from githi.llm.client import Completion
from githi.sandbox.policy import SandboxPolicy
from githi.tools.base import Tool, ToolResult
from githi.tools.registry import ToolRegistry
from githi.types import (
    AssistantMessage,
    ThinkingLevel,
    ToolCall,
    Usage,
    UserMessage,
)


class ScriptedLLM:
    def __init__(self, script):
        self.script = list(script)
        self.calls: list[list] = []
        self.kwargs: list[dict] = []
        self.tools_seen: list[list] = []
        self.model = "test-model"

    def complete(self, messages, tools, **kw):
        self.calls.append(list(messages))
        self.kwargs.append(dict(kw))
        self.tools_seen.append([t["function"]["name"] for t in (tools or [])])
        message = self.script.pop(0) if self.script else AssistantMessage(content="done")
        return Completion(message=message, usage=Usage(input_tokens=1))


def _tool(name):
    return Tool(name=name, description="d", parameters={"type": "object"},
                run=lambda a, c: ToolResult(content="ran"))


def _agent(tmp_path, script=None, tools=("bash", "read", "write"), **cfg):
    return Agent(llm=ScriptedLLM(script or [AssistantMessage(content="done")]),
                 tools=ToolRegistry([_tool(n) for n in tools]),
                 policy=SandboxPolicy(root=tmp_path), config=AgentConfig(**cfg))


def _call(i, name="bash"):
    return AssistantMessage(content="", tool_calls=[ToolCall(f"c{i}", name, {})])


class TestNextTurnQueue:
    def test_next_turn_content_reaches_the_model(self, tmp_path):
        agent = _agent(tmp_path, [_call(0), AssistantMessage(content="done")])
        agent.next_turn(UserMessage("recalled: the repo uses uv"))
        agent.run("go")
        assert any("recalled: the repo uses uv" in (getattr(m, "content", "") or "")
                   for call in agent.llm.calls for m in call)

    def test_next_turn_is_a_separate_queue_from_steering(self, tmp_path):
        """Clearing one must not clear the other, or a caller cancelling a
        correction would silently drop injected context too."""
        agent = _agent(tmp_path)
        agent.next_turn(UserMessage("context"))
        agent.steer(UserMessage("correction"))
        agent.clear_steering_queue()
        assert agent.has_queued_messages()

    def test_clear_all_queues_includes_next_turn(self, tmp_path):
        agent = _agent(tmp_path)
        agent.next_turn(UserMessage("context"))
        agent.clear_all_queues()
        assert not agent.has_queued_messages()

    def test_next_turn_does_not_restart_a_finished_run(self, tmp_path):
        """The difference from follow-up: this adds context to a turn that was
        going to happen, it does not manufacture one."""
        agent = _agent(tmp_path, [AssistantMessage(content="done")])
        agent.next_turn(UserMessage("context"))
        agent.run("go")
        assert len(agent.llm.calls) == 1


class TestAppendMessage:
    def test_appends_without_running_a_turn(self, tmp_path):
        agent = _agent(tmp_path)
        agent.append_message(UserMessage("known context"))
        assert agent.llm.calls == []
        assert any("known context" in (getattr(m, "content", "") or "")
                   for m in agent.state.messages)

    def test_appended_content_is_visible_to_the_next_run(self, tmp_path):
        agent = _agent(tmp_path)
        agent.append_message(UserMessage("known context"))
        agent.run("go")
        assert any("known context" in (getattr(m, "content", "") or "")
                   for m in agent.llm.calls[0])

    def test_appended_messages_are_persisted(self, tmp_path):
        session = tmp_path / "s.jsonl"
        agent = _agent(tmp_path, session_path=session)
        agent.append_message(UserMessage("known context"))
        assert session.exists() and "known context" in session.read_text()


class TestModelAndThinking:
    def test_set_model_takes_effect_on_the_next_turn(self, tmp_path):
        agent = _agent(tmp_path, [_call(0), AssistantMessage(content="done")])
        agent.set_model("bigger-model")
        agent.run("go")
        assert agent.llm.kwargs[0].get("model") == "bigger-model"

    def test_set_thinking_level(self, tmp_path):
        agent = _agent(tmp_path, [AssistantMessage(content="done")])
        agent.set_thinking_level(ThinkingLevel.HIGH)
        agent.run("go")
        assert agent.llm.kwargs[0].get("thinking_level") is ThinkingLevel.HIGH

    def test_unset_model_sends_no_override(self, tmp_path):
        """The client's own default must win when nothing was set, rather than
        being shadowed by a None the loop passed explicitly."""
        agent = _agent(tmp_path)
        agent.run("go")
        assert "model" not in agent.llm.kwargs[0]

    def test_the_transcript_survives_a_model_change(self, tmp_path):
        """The point of changing model in place: escalate for the hard turn
        without losing what the cheap turns established."""
        agent = _agent(tmp_path, [_call(0), AssistantMessage(content="done")])
        agent.run("go")
        before = len(agent.state.messages)
        agent.set_model("bigger-model")
        assert len(agent.state.messages) == before


class TestActiveTools:
    def test_narrowing_the_offered_tools(self, tmp_path):
        agent = _agent(tmp_path)
        agent.set_active_tools(["bash"])
        agent.run("go")
        assert agent.llm.tools_seen[0] == ["bash"]

    def test_the_registry_still_holds_the_others(self, tmp_path):
        """Narrowed, not removed -- a phase machine has to re-offer them."""
        agent = _agent(tmp_path)
        agent.set_active_tools(["bash"])
        agent.set_active_tools(["bash", "read", "write"])
        agent.run("go")
        assert sorted(agent.llm.tools_seen[0]) == ["bash", "read", "write"]

    def test_an_unknown_tool_name_is_rejected(self, tmp_path):
        """A typo would otherwise narrow the set silently, surfacing much later
        as the model inexplicably refusing to act."""
        agent = _agent(tmp_path)
        with pytest.raises(ValueError, match="nosuchtool"):
            agent.set_active_tools(["nosuchtool"])

    def test_a_narrowed_tool_cannot_be_called(self, tmp_path):
        agent = _agent(tmp_path, [_call(0, "write"), AssistantMessage(content="done")])
        agent.set_active_tools(["bash"])
        agent.run("go")
        results = [m for m in agent.state.messages
                   if type(m).__name__ == "ToolResultMessage"]
        assert results and results[0].is_error

    def test_active_tools_defaults_to_everything(self, tmp_path):
        agent = _agent(tmp_path)
        agent.run("go")
        assert sorted(agent.llm.tools_seen[0]) == ["bash", "read", "write"]


class TestExplicitCompaction:
    def test_compact_now_shrinks_the_transcript(self, tmp_path):
        agent = _agent(tmp_path)
        for i in range(30):
            agent.append_message(UserMessage("x" * 2000))
        before = len(agent.state.messages)
        agent.compact_now(limit_tokens=1_000)
        assert len(agent.state.messages) < before

    def test_compact_now_uses_the_configured_summarizer(self, tmp_path):
        calls = []
        agent = _agent(tmp_path, summarizer=lambda m, previous_summary=None:
                       calls.append(1) or "## Goal\nx")
        for i in range(30):
            agent.append_message(UserMessage("x" * 2000))
        agent.compact_now(limit_tokens=1_000)
        assert calls

    def test_compact_now_on_a_short_transcript_is_a_no_op(self, tmp_path):
        agent = _agent(tmp_path)
        agent.append_message(UserMessage("short"))
        before = list(agent.state.messages)
        agent.compact_now(limit_tokens=100_000)
        assert agent.state.messages == before
