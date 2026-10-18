"""The system prompt belongs in the transcript once.

`Agent.run` appends a fresh `SystemMessage` unconditionally, so a second run
on the same agent puts a second identical copy in the transcript, a third run
a third, and so on. Surfaced by inspecting a real two-run transcript, not by
any test -- every existing test either ran once or only checked that the
prompt was present, which duplication does not disturb.

The cost is concrete. The prompt is one of the largest fixed blocks in the
payload, it is re-sent on every subsequent turn, and it sits at the front
where the cache breakpoint goes -- so duplication both inflates the bill and
pushes the cacheable prefix out of shape. In the session file it is worse: a
reader cannot tell a re-issued prompt from a deliberately changed one.

Multi-run agents are the normal case, not an edge case: that is what
`follow_up`, `rewind_to` and any interactive session do.
"""

from __future__ import annotations

import pytest

from githi.agent.agent import Agent, AgentConfig
from githi.llm.client import Completion
from githi.sandbox.policy import SandboxPolicy
from githi.tools.registry import ToolRegistry
from githi.types import AssistantMessage, SystemMessage, Usage


class ScriptedLLM:
    def __init__(self, script):
        self.script = list(script)
        self.calls: list[list] = []
        self.model = "test-model"

    def complete(self, messages, tools, **kw):
        self.calls.append(list(messages))
        message = self.script.pop(0) if self.script else AssistantMessage(content="done")
        return Completion(message=message, usage=Usage(input_tokens=1))


def _agent(tmp_path, **cfg):
    return Agent(llm=ScriptedLLM([AssistantMessage(content="a"),
                                  AssistantMessage(content="b"),
                                  AssistantMessage(content="c")]),
                 tools=ToolRegistry([]), policy=SandboxPolicy(root=tmp_path),
                 config=AgentConfig(**cfg))


def _system_count(messages):
    return sum(1 for m in messages if isinstance(m, SystemMessage))


class TestSingleSystemPrompt:
    def test_one_run_has_one_system_prompt(self, tmp_path):
        agent = _agent(tmp_path)
        agent.run("first")
        assert _system_count(agent.state.messages) == 1

    def test_a_second_run_does_not_add_another(self, tmp_path):
        agent = _agent(tmp_path)
        agent.run("first")
        agent.run("second")
        assert _system_count(agent.state.messages) == 1

    def test_the_payload_carries_one_copy(self, tmp_path):
        """The billing consequence: the prompt is re-sent every turn."""
        agent = _agent(tmp_path)
        agent.run("first")
        agent.run("second")
        assert _system_count(agent.llm.calls[-1]) == 1

    def test_the_session_file_records_it_once(self, tmp_path):
        from githi.harness.session import Session

        path = tmp_path / "s.jsonl"
        agent = _agent(tmp_path, session_path=path)
        agent.run("first")
        agent.run("second")
        assert _system_count(Session(path).load()) == 1

    def test_reset_starts_a_fresh_prompt(self, tmp_path):
        """After a reset the transcript is empty, so the prompt must be
        written again -- suppressing it there would leave the agent with no
        instructions at all."""
        agent = _agent(tmp_path)
        agent.run("first")
        agent.reset()
        agent.run("second")
        assert _system_count(agent.state.messages) == 1

    def test_the_prompt_is_still_first(self, tmp_path):
        agent = _agent(tmp_path)
        agent.run("first")
        agent.run("second")
        assert isinstance(agent.state.messages[0], SystemMessage)

    def test_the_user_prompt_is_still_appended_each_run(self, tmp_path):
        """Only the *system* message is de-duplicated; each run's own prompt
        is new content and must appear."""
        agent = _agent(tmp_path)
        agent.run("first")
        agent.run("second")
        contents = [getattr(m, "content", "") for m in agent.state.messages]
        assert "first" in contents and "second" in contents
