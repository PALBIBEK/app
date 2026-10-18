"""The `remember` tool: the agent's own write path into memory.

Without it, memory can only be filled by whoever wrote the harness, which
makes it configuration rather than memory. The agent has to be able to record
something it was just told.

Model-driven rather than automatic, deliberately. Deciding on the agent's
behalf what is "worth remembering" is the fitness-signal problem -- and both
Grok and Hermes turned out to have no outcome-based signal for it either. A
tool makes the decision explicit, visible in the transcript, and auditable
afterwards, and the model is genuinely good at telling a durable preference
("I always mean this repo") from a transient fact ("the latest tag is 1.5.3")
when asked to.

The description matters as much as the code. A tool the model does not know
when to reach for is a tool it will not use, so it names what belongs in
memory and what does not.
"""

from __future__ import annotations

import pytest

from githi.memory.service import MemoryService
from githi.sandbox.policy import SandboxPolicy
from githi.tools.base import ToolContext
from githi.tools.remember import remember_tool


@pytest.fixture
def memory(tmp_path):
    return MemoryService(root=tmp_path / "mem")


@pytest.fixture
def ctx(tmp_path):
    return ToolContext(policy=SandboxPolicy(root=tmp_path))


class TestWriting:
    def test_a_fact_is_stored(self, memory, ctx):
        remember_tool(memory).run({"fact": "The user prefers dates as YYYY-MM-DD."}, ctx)
        assert "YYYY-MM-DD" in memory.read()

    def test_the_result_confirms_what_was_stored(self, memory, ctx):
        """The model needs to see the write happened, or it will repeat it."""
        result = remember_tool(memory).run({"fact": "Prefers uv over pip."}, ctx)
        assert not result.is_error and "uv over pip" in result.text

    def test_an_empty_fact_is_refused(self, memory, ctx):
        result = remember_tool(memory).run({"fact": "   "}, ctx)
        assert result.is_error

    def test_a_missing_fact_argument_is_refused(self, memory, ctx):
        assert remember_tool(memory).run({}, ctx).is_error

    def test_storing_twice_does_not_duplicate(self, memory, ctx):
        tool = remember_tool(memory)
        fact = "The user's main project is pallets/flask."
        tool.run({"fact": fact}, ctx)
        tool.run({"fact": fact}, ctx)
        assert memory.read().count(fact) == 1

    def test_several_facts_accumulate(self, memory, ctx):
        tool = remember_tool(memory)
        tool.run({"fact": "First preference."}, ctx)
        tool.run({"fact": "Second preference."}, ctx)
        assert "First preference." in memory.read() and "Second preference." in memory.read()


class TestPersistence:
    def test_the_fact_survives_into_a_new_service(self, tmp_path, ctx):
        """The whole point: a preference stated today applies tomorrow."""
        root = tmp_path / "mem"
        remember_tool(MemoryService(root=root)).run(
            {"fact": "When the user says 'my main project' they mean pallets/flask."}, ctx)

        hits = MemoryService(root=root).recall("what is my main project")
        assert any("pallets/flask" in h for h in hits)


class TestContract:
    def test_the_description_says_when_to_use_it(self, memory):
        """A tool the model cannot tell when to reach for goes unused."""
        description = remember_tool(memory).description.lower()
        assert "prefer" in description or "durable" in description

    def test_the_description_warns_against_transient_facts(self, memory):
        """Storing 'the latest tag is 1.5.3' poisons memory within a week --
        a stale memory is worse than none, because it is confidently wrong."""
        description = remember_tool(memory).description.lower()
        assert "not" in description and ("change" in description or "temporar" in description)

    def test_it_declares_a_fact_parameter(self, memory):
        assert "fact" in remember_tool(memory).parameters["properties"]


class TestResilience:
    def test_a_broken_store_reports_rather_than_raises(self, ctx):
        class Broken:
            def remember(self, text, **kw):
                raise RuntimeError("disk on fire")

        result = remember_tool(Broken()).run({"fact": "something"}, ctx)
        assert result.is_error and "disk on fire" in result.text

    def test_no_memory_configured_is_a_clear_error(self, ctx):
        """Better an explicit message than a tool that silently does nothing
        and reports success."""
        result = remember_tool(None).run({"fact": "something"}, ctx)
        assert result.is_error and "memory" in result.text.lower()
