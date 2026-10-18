"""The summarizer has to reach the actual `compact` call.

A capability that exists in a module but is never invoked by the loop is the
failure mode this project has already hit once: the memory module was fully
built and fully tested while the agent never called it. So these tests assert
on the *agent*, not on `compaction.py` -- they fail if the wiring is missing
even when every unit test for summarization passes.

The strongest check is the last one: the summary must appear in the payload
actually sent to the model. Anything short of that (it was generated, it was
stored on the state) is compatible with the model never seeing it.
"""

from __future__ import annotations

import pytest

from githi.agent.agent import Agent, AgentConfig
from githi.harness.compaction import SUMMARY_MARKER
from githi.llm.client import Completion
from githi.sandbox.policy import SandboxPolicy
from githi.tools.base import Tool, ToolResult
from githi.tools.registry import ToolRegistry
from githi.types import AssistantMessage, ToolCall, Usage


class ScriptedLLM:
    def __init__(self, script):
        self.script = list(script)
        self.calls: list[list] = []
        self.model = "test-model"

    def complete(self, messages, tools, **kw):
        self.calls.append(list(messages))
        message = self.script.pop(0) if self.script else AssistantMessage(content="done")
        return Completion(message=message, usage=Usage(input_tokens=1))


def _bulky_tool():
    """Emits enough output that a handful of calls blows a small budget."""
    return Tool(name="bash", description="d", parameters={"type": "object"},
                run=lambda a, c: ToolResult(content="x" * 4000))


def _agent(tmp_path, summarizer=None, turns=12):
    script = [AssistantMessage(content="", tool_calls=[ToolCall(f"c{i}", "bash", {})])
              for i in range(turns)] + [AssistantMessage(content="final")]
    return Agent(llm=ScriptedLLM(script), tools=ToolRegistry([_bulky_tool()]),
                 policy=SandboxPolicy(root=tmp_path),
                 config=AgentConfig(max_turns=turns + 1, context_limit_tokens=800,
                                    summarizer=summarizer))


class Recording:
    def __init__(self):
        self.calls = 0

    def __call__(self, messages, previous_summary=None):
        self.calls += 1
        return "## Goal\nreconstructed goal\n\n## Progress\n- [x] ran bash"


class TestWiring:
    def test_the_agent_accepts_a_summarizer(self, tmp_path):
        assert AgentConfig(summarizer=None).summarizer is None

    def test_the_summarizer_is_invoked_during_a_long_run(self, tmp_path):
        summarizer = Recording()
        _agent(tmp_path, summarizer).run("go")
        assert summarizer.calls > 0, "compaction ran without calling the summarizer"

    def test_the_summary_reaches_the_wire_payload(self, tmp_path):
        """The only check that proves the model can actually see it."""
        summarizer = Recording()
        agent = _agent(tmp_path, summarizer)
        agent.run("go")
        assert any("reconstructed goal" in getattr(m, "content", "") or ""
                   for call in agent.llm.calls for m in call)

    def test_the_checkpoint_is_marked_so_it_can_be_updated_later(self, tmp_path):
        agent = _agent(tmp_path, Recording())
        agent.run("go")
        assert any(SUMMARY_MARKER in (getattr(m, "content", "") or "")
                   for call in agent.llm.calls for m in call)

    def test_without_a_summarizer_the_run_still_completes(self, tmp_path):
        """Summarization is an enhancement; the agent must not depend on it."""
        assert _agent(tmp_path).run("go").output == "final"

    def test_a_failing_summarizer_does_not_break_the_run(self, tmp_path):
        def explode(messages, previous_summary=None):
            raise RuntimeError("provider 503")

        assert _agent(tmp_path, explode).run("go").output == "final"

    def test_a_short_run_never_calls_the_summarizer(self, tmp_path):
        summarizer = Recording()
        agent = Agent(llm=ScriptedLLM([AssistantMessage(content="done")]),
                      tools=ToolRegistry([_bulky_tool()]),
                      policy=SandboxPolicy(root=tmp_path),
                      config=AgentConfig(summarizer=summarizer))
        agent.run("go")
        assert summarizer.calls == 0
