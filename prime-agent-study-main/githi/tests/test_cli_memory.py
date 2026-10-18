"""The CLI has to actually hand the agent a memory.

`MemoryService` was built, `AgentConfig.memory` was wired, the `remember` tool
was written and tested -- and `cli.py` passed none of it, so every real
session had memory switched off. Nobody running `./run.sh` could have used it.

That is the fifth time in this project a capability existed, passed its tests,
and was never reached from the entry point a person actually uses. So these
tests assert on `build_agent`, the function the CLI calls, rather than on any
module underneath it.

Recall is announced when it fires. Silent injection is what made the earlier
0%-improvement bug invisible for so long: the only way to know whether memory
did anything was to read the wire payload.
"""

from __future__ import annotations

import io

import pytest

from githi.cli import build_agent, _parse_args


@pytest.fixture(autouse=True)
def _fake_key(monkeypatch):
    """`build_agent` constructs a real client, which refuses to exist without
    a key. No request is ever sent by these tests."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key-not-used")


def _args(argv):
    return _parse_args(argv)


class TestMemoryIsWired:
    def test_memory_is_on_by_default(self, tmp_path):
        agent, _ = build_agent(_args(["--workspace", str(tmp_path), "hi"]))
        assert agent.config.memory is not None

    def test_it_can_be_turned_off(self, tmp_path):
        agent, _ = build_agent(
            _args(["--workspace", str(tmp_path), "--no-memory", "hi"]))
        assert agent.config.memory is None

    def test_the_remember_tool_is_registered(self, tmp_path):
        """Without it the agent can never write anything, so memory is
        read-only configuration rather than memory."""
        agent, _ = build_agent(_args(["--workspace", str(tmp_path), "hi"]))
        assert "remember" in agent.tools.names()

    def test_no_remember_tool_when_memory_is_off(self, tmp_path):
        agent, _ = build_agent(
            _args(["--workspace", str(tmp_path), "--no-memory", "hi"]))
        assert "remember" not in agent.tools.names()

    def test_memory_lives_in_the_workspace(self, tmp_path):
        """So it persists between sessions like the rest of the workspace,
        and a different --workspace gets a different memory."""
        agent, _ = build_agent(_args(["--workspace", str(tmp_path), "hi"]))
        assert str(tmp_path) in str(agent.config.memory.root)

    def test_a_fact_written_now_is_recalled_by_a_later_agent(self, tmp_path):
        """The whole point, at the level the CLI operates."""
        first, _ = build_agent(_args(["--workspace", str(tmp_path), "hi"]))
        first.config.memory.remember(
            "The user's main project is the pallets/flask repository.")

        second, _ = build_agent(_args(["--workspace", str(tmp_path), "hi"]))
        assert any("flask" in hit
                   for hit in second.config.memory.recall("what is my main project"))


class TestRecallIsVisible:
    def test_the_printer_announces_a_recall(self):
        """Five bugs in this project were invisible wiring. Memory injecting
        silently would be the sixth."""
        from githi.agent.events import Event, EventType
        from githi.cli import StreamPrinter

        err = io.StringIO()
        printer = StreamPrinter(out=io.StringIO(), err=err, verbose=True)
        printer(Event(EventType.MEMORY_RECALL, {"count": 2}))
        assert "memor" in err.getvalue().lower()
