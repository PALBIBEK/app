"""The session tree has to be reachable from the agent.

A tree nothing calls is a data structure, not a capability -- the same trap
the memory module sits in. These tests assert on `Agent`, so they fail if the
branching exists only in `session.py`.

What the agent gains:

* **`rewind_to(entry_id)`** -- drop back to an earlier point and continue from
  there, with the abandoned attempt still on disk. This is the operation the
  tree exists for.
* **provenance entries** -- `set_model` and `set_active_tools` are recorded, so
  a session file explains *why* turn 12 behaved differently from turn 11.
  Without it the transcript shows the change of behaviour and not its cause.

`rewind_to` truncates the in-memory transcript as well as re-pointing the
file. Re-pointing alone would leave the agent still holding the messages it
supposedly abandoned, and the next request would send them.
"""

from __future__ import annotations

import pytest

from githi.agent.agent import Agent, AgentConfig
from githi.harness.session import Session
from githi.llm.client import Completion
from githi.sandbox.policy import SandboxPolicy
from githi.tools.base import Tool, ToolResult
from githi.tools.registry import ToolRegistry
from githi.types import AssistantMessage, Usage, UserMessage


class ScriptedLLM:
    def __init__(self, script):
        self.script = list(script)
        self.calls: list[list] = []
        self.model = "test-model"

    def complete(self, messages, tools, **kw):
        self.calls.append(list(messages))
        message = self.script.pop(0) if self.script else AssistantMessage(content="done")
        return Completion(message=message, usage=Usage(input_tokens=1))


def _agent(tmp_path, script=None, **cfg):
    cfg.setdefault("session_path", tmp_path / "s.jsonl")
    return Agent(llm=ScriptedLLM(script or [AssistantMessage(content="done")]),
                 tools=ToolRegistry([Tool(name="bash", description="d",
                                          parameters={"type": "object"},
                                          run=lambda a, c: ToolResult(content="ran"))]),
                 policy=SandboxPolicy(root=tmp_path), config=AgentConfig(**cfg))


class TestRewind:
    def test_rewind_drops_later_messages_from_the_transcript(self, tmp_path):
        agent = _agent(tmp_path, [AssistantMessage(content="first"),
                                  AssistantMessage(content="second")])
        agent.run("go")
        mark = agent.mark()
        agent.run("again")
        agent.rewind_to(mark)
        assert not any("second" in (getattr(m, "content", "") or "")
                       for m in agent.state.messages)

    def test_the_kept_prefix_survives(self, tmp_path):
        agent = _agent(tmp_path, [AssistantMessage(content="first"),
                                  AssistantMessage(content="second")])
        agent.run("go")
        mark = agent.mark()
        agent.run("again")
        agent.rewind_to(mark)
        assert any("first" in (getattr(m, "content", "") or "")
                   for m in agent.state.messages)

    def test_the_next_request_does_not_carry_the_abandoned_turn(self, tmp_path):
        """Re-pointing the file but keeping the messages in memory would send
        the very content the caller rewound past."""
        agent = _agent(tmp_path, [AssistantMessage(content="first"),
                                  AssistantMessage(content="second"),
                                  AssistantMessage(content="third")])
        agent.run("go")
        mark = agent.mark()
        agent.run("again")
        agent.rewind_to(mark)
        agent.run("third try")
        assert not any("second" in (getattr(m, "content", "") or "")
                       for m in agent.llm.calls[-1])

    def test_the_abandoned_branch_is_still_on_disk(self, tmp_path):
        """The fork materializes on the next *write*, not on the rewind --
        `rewind_to` only moves where the next entry will hang from."""
        session_path = tmp_path / "s.jsonl"
        agent = _agent(tmp_path, [AssistantMessage(content="first"),
                                  AssistantMessage(content="second"),
                                  AssistantMessage(content="third")],
                       session_path=session_path)
        agent.run("go")
        mark = agent.mark()
        agent.run("again")
        agent.rewind_to(mark)
        agent.run("third try")

        session = Session(session_path)
        assert len(session.leaves()) == 2
        branches = [[m.content for m in session.load(leaf.id)]
                    for leaf in session.leaves()]
        assert any("second" in b for b in branches)
        assert any("third" in b for b in branches)

    def test_mark_without_a_session_is_none(self, tmp_path):
        agent = Agent(llm=ScriptedLLM([]), tools=ToolRegistry([]),
                      policy=SandboxPolicy(root=tmp_path), config=AgentConfig())
        assert agent.mark() is None

    def test_rewinding_without_a_session_is_refused(self, tmp_path):
        """Better a clear error than silently truncating the transcript with
        no file to restore it from."""
        agent = Agent(llm=ScriptedLLM([]), tools=ToolRegistry([]),
                      policy=SandboxPolicy(root=tmp_path), config=AgentConfig())
        with pytest.raises(ValueError, match="session"):
            agent.rewind_to("anything")


class TestProvenance:
    def test_a_model_change_is_recorded(self, tmp_path):
        session_path = tmp_path / "s.jsonl"
        agent = _agent(tmp_path, session_path=session_path)
        agent.run("go")
        agent.set_model("bigger-model")
        assert any(e.type == "model_change"
                   for e in Session(session_path).entries())

    def test_a_tool_narrowing_is_recorded(self, tmp_path):
        session_path = tmp_path / "s.jsonl"
        agent = _agent(tmp_path, session_path=session_path)
        agent.run("go")
        agent.set_active_tools(["bash"])
        assert any(e.type == "active_tools_change"
                   for e in Session(session_path).entries())

    def test_provenance_entries_are_not_replayed_as_messages(self, tmp_path):
        """They explain the run; they are not part of the conversation."""
        session_path = tmp_path / "s.jsonl"
        agent = _agent(tmp_path, session_path=session_path)
        agent.run("go")
        agent.set_model("bigger-model")
        loaded = Session(session_path).load()
        assert all(getattr(m, "content", "") != "bigger-model" for m in loaded)

    def test_changes_without_a_session_do_not_raise(self, tmp_path):
        agent = Agent(llm=ScriptedLLM([]), tools=ToolRegistry([]),
                      policy=SandboxPolicy(root=tmp_path), config=AgentConfig())
        agent.set_model("bigger-model")          # must not raise
        assert agent._model == "bigger-model"


class TestRewindCarriesForward:
    """The abandoned branch's lesson must reach the retry.

    Without this the tree gives you a second attempt and takes away everything
    the first one learned -- so the retry walks into the same dead end, and
    pays for it again.
    """

    def test_the_summary_reaches_the_next_request(self, tmp_path):
        agent = _agent(tmp_path, [AssistantMessage(content="first"),
                                  AssistantMessage(content="dead end"),
                                  AssistantMessage(content="second try")],
                       summarizer=lambda m, **kw: "## What did not work\n- scanning every commit")
        agent.run("go")
        mark = agent.mark()
        agent.run("continue")
        agent.rewind_to(mark)
        agent.run("try differently")
        assert any("scanning every commit" in (getattr(m, "content", "") or "")
                   for m in agent.llm.calls[-1])

    def test_it_is_recorded_in_the_session(self, tmp_path):
        session_path = tmp_path / "s.jsonl"
        agent = _agent(tmp_path, [AssistantMessage(content="first"),
                                  AssistantMessage(content="dead end")],
                       session_path=session_path,
                       summarizer=lambda m, **kw: "## Goal\nthe abandoned attempt")
        agent.run("go")
        mark = agent.mark()
        agent.run("continue")
        agent.rewind_to(mark)
        assert any(e.type == "branch_summary" for e in Session(session_path).entries())

    def test_no_summarizer_still_rewinds(self, tmp_path):
        """Summarising is an enhancement; losing it must not lose the rewind."""
        agent = _agent(tmp_path, [AssistantMessage(content="first"),
                                  AssistantMessage(content="second")])
        agent.run("go")
        mark = agent.mark()
        agent.run("continue")
        agent.rewind_to(mark)
        assert not any("second" in (getattr(m, "content", "") or "")
                       for m in agent.state.messages)

    def test_a_failing_summarizer_still_rewinds(self, tmp_path):
        def explode(messages, **kw):
            raise RuntimeError("provider down")

        agent = _agent(tmp_path, [AssistantMessage(content="first"),
                                  AssistantMessage(content="second")],
                       summarizer=explode)
        agent.run("go")
        mark = agent.mark()
        agent.run("continue")
        agent.rewind_to(mark)
        assert not any("second" in (getattr(m, "content", "") or "")
                       for m in agent.state.messages)

    def test_summarising_can_be_turned_off(self, tmp_path):
        calls = []
        agent = _agent(tmp_path, [AssistantMessage(content="first"),
                                  AssistantMessage(content="second")],
                       summarizer=lambda m, **kw: calls.append(1) or "x")
        agent.run("go")
        mark = agent.mark()
        agent.run("continue")
        agent.rewind_to(mark, summarize=False)
        assert calls == []


class TestSessionLifecycle:
    def test_agent_resumes_the_active_session_branch(self, tmp_path):
        path = tmp_path / "s.jsonl"
        first = _agent(tmp_path, session_path=path)
        first.run("remember this")

        resumed = _agent(tmp_path, session_path=path)
        assert any("remember this" in (getattr(message, "content", "") or "")
                   for message in resumed.state.messages)

    def test_new_session_rotates_to_a_fresh_file(self, tmp_path):
        path = tmp_path / "session.jsonl"
        agent = _agent(tmp_path, session_path=path)
        agent.run("old")
        old_path = path

        new_path = agent.new_session()

        assert new_path != old_path and new_path.exists()
        assert agent.session_path == new_path
        assert agent.state.messages == []
        assert "old" in old_path.read_text()

    def test_manual_compaction_is_recorded_as_a_replayable_snapshot(self, tmp_path):
        path = tmp_path / "s.jsonl"
        agent = _agent(tmp_path, session_path=path)
        for i in range(30):
            agent.append_message(UserMessage(f"message {i} " + "x" * 2000))
        agent.compact_now(limit_tokens=1_000)

        resumed = _agent(tmp_path, session_path=path)
        assert any(e.type == "compaction" for e in Session(path).entries())
        assert resumed.state.messages == agent.state.messages
