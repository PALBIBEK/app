"""Recall has to reach the model, not merely happen.

The previous attempt at memory measured **0% improvement**. The cause was not
retrieval: the right text was found and stored on the run state. It was that
nothing put it in the request. Every unit test passed the whole time.

So the load-bearing assertion in this file is `agent.llm.calls[-1]` -- the
payload actually sent. "Recall was invoked", "the text is on the state", and
"the hook returned it" were all true last time and all worthless.

The design decisions being pinned:

* **Once per user prompt, not per turn.** The query is what the user asked;
  re-searching each agent turn hunts a moving target and re-injects the same
  text every turn, paying for it repeatedly.
* **Through the `next_turn` queue**, which exists precisely for this: context
  that is neither a correction (steering) nor a reason to start another turn
  (follow-up).
* **Labelled.** Unlabelled recall reads as something the user just said, and
  the model will attribute a remembered fact back to them.
* **Never fatal.** A broken memory store costs the run its recall, nothing
  more.
"""

from __future__ import annotations

import pytest

from githi.agent.agent import Agent, AgentConfig
from githi.llm.client import Completion
from githi.memory.service import MemoryService
from githi.sandbox.policy import SandboxPolicy
from githi.tools.base import Tool, ToolResult
from githi.tools.registry import ToolRegistry
from githi.types import AssistantMessage, ToolCall, Usage


class ScriptedLLM:
    def __init__(self, script=None):
        self.script = list(script or [])
        self.calls: list[list] = []
        self.model = "test-model"

    def complete(self, messages, tools, **kw):
        self.calls.append(list(messages))
        message = self.script.pop(0) if self.script else AssistantMessage(content="done")
        return Completion(message=message, usage=Usage(input_tokens=1))


def _text_of(messages) -> str:
    return "\n".join(str(getattr(m, "content", "") or "") for m in messages)


@pytest.fixture
def memory(tmp_path):
    service = MemoryService(root=tmp_path / "mem")
    service.remember("GitHub's search/issues endpoint counts pull requests as "
                     "issues. Use `type:issue` to exclude them.")
    return service


def _agent(tmp_path, memory=None, script=None):
    return Agent(llm=ScriptedLLM(script), tools=ToolRegistry([
                     Tool(name="bash", description="d", parameters={"type": "object"},
                          run=lambda a, c: ToolResult(content="ran"))]),
                 policy=SandboxPolicy(root=tmp_path),
                 config=AgentConfig(memory=memory))


class TestRecallReachesTheWire:
    def test_the_recalled_text_is_in_the_request(self, tmp_path, memory):
        """The assertion that would have caught the 0% result."""
        agent = _agent(tmp_path, memory)
        agent.run("how many open issues does pallets/flask have?")
        assert "type:issue" in _text_of(agent.llm.calls[-1])

    def test_it_is_labelled_as_memory(self, tmp_path, memory):
        agent = _agent(tmp_path, memory)
        agent.run("how many open issues does pallets/flask have?")
        assert "memory" in _text_of(agent.llm.calls[-1]).lower()

    def test_the_user_prompt_is_still_there(self, tmp_path, memory):
        """Injection must add to the request, never replace part of it."""
        agent = _agent(tmp_path, memory)
        agent.run("how many open issues does pallets/flask have?")
        assert "pallets/flask" in _text_of(agent.llm.calls[-1])

    def test_an_irrelevant_prompt_injects_nothing(self, tmp_path, memory):
        """Recall on every turn regardless of relevance is how the section
        becomes noise the model learns to skip."""
        agent = _agent(tmp_path, memory)
        agent.run("what is the capital of France?")
        assert "type:issue" not in _text_of(agent.llm.calls[-1])

    def test_no_memory_configured_changes_nothing(self, tmp_path):
        agent = _agent(tmp_path)
        assert agent.run("anything").output == "done"


class TestRecallFrequency:
    def test_recall_runs_once_per_prompt_not_once_per_turn(self, tmp_path, memory):
        """Re-searching every agent turn searches against a moving target and
        re-injects the same text, paying for it each time."""
        queries: list[str] = []
        original = memory.recall_block
        memory.recall_block = lambda q, **kw: (queries.append(q), original(q, **kw))[1]

        agent = _agent(tmp_path, memory, script=[
            AssistantMessage(content="", tool_calls=[ToolCall("c1", "bash", {})]),
            AssistantMessage(content="", tool_calls=[ToolCall("c2", "bash", {})]),
            AssistantMessage(content="done")])
        agent.run("how many open issues does pallets/flask have?")
        assert len(queries) == 1

    def test_the_query_is_the_user_prompt(self, tmp_path, memory):
        seen: list[str] = []
        memory.recall_block = lambda q, **kw: (seen.append(q), "")[1]
        _agent(tmp_path, memory).run("count the open issues")
        assert seen == ["count the open issues"]

    def test_a_second_run_recalls_again(self, tmp_path, memory):
        """Each user prompt is a new query -- the second question is not
        served by the first one's recall."""
        seen: list[str] = []
        memory.recall_block = lambda q, **kw: (seen.append(q), "")[1]
        agent = _agent(tmp_path, memory, script=[AssistantMessage(content="a"),
                                                 AssistantMessage(content="b")])
        agent.run("first question")
        agent.run("second question")
        assert seen == ["first question", "second question"]


class TestResilience:
    def test_a_broken_memory_does_not_end_the_run(self, tmp_path):
        class Broken:
            def recall_block(self, query, **kw):
                raise RuntimeError("index exploded")

        assert _agent(tmp_path, Broken()).run("go").output == "done"

    def test_an_empty_store_injects_nothing(self, tmp_path):
        empty = MemoryService(root=tmp_path / "empty")
        agent = _agent(tmp_path, empty)
        agent.run("anything")
        assert "memory" not in _text_of(agent.llm.calls[-1]).lower()
