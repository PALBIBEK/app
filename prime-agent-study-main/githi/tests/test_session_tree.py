"""Session as a tree, not a list (port of `harness/session/*`).

githi's session is a flat append-only log, so the transcript can only ever
grow. pi's is a tree: every entry records its parent, and `get_branch` walks
a leaf back to the root. The difference is that you can **rewind**.

That matters because agent runs go wrong in the middle. With a flat log, an
agent that took a bad turn at step 5 leaves you two options -- keep the bad
context, or throw away all five steps and start over. With a tree you fork
from step 4, try a different approach, and the failed branch is still there:
you can compare the two, and you have not paid again for the four steps that
were fine.

It also makes compaction non-destructive. Today `compact` mutates the
transcript and the dropped span is gone; as a tree entry, the pre-compaction
branch stays on disk while the model sees the summary.

**Backwards compatibility is a hard requirement.** Sessions written by the
current code are bare message objects with no `id` and no `parent_id`. They
must keep loading, in order, or every existing session file becomes garbage
the moment this lands. A legacy file is read as one linear branch, which is
exactly what it is.
"""

from __future__ import annotations

import json

import pytest

from githi.harness.session import Session
from githi.types import AssistantMessage, ToolCall, ToolResultMessage, UserMessage


@pytest.fixture
def session(tmp_path):
    return Session(tmp_path / "s.jsonl")


class TestBackwardsCompatibility:
    def test_a_legacy_flat_file_still_loads(self, tmp_path):
        """Files written before entries had ids are bare message objects."""
        path = tmp_path / "old.jsonl"
        path.write_text(
            json.dumps({"role": "user", "content": "one"}) + "\n"
            + json.dumps({"role": "assistant", "content": "two", "tool_calls": []}) + "\n",
            encoding="utf-8")
        loaded = Session(path).load()
        assert [m.content for m in loaded] == ["one", "two"]

    def test_a_legacy_file_reads_as_one_branch(self, tmp_path):
        path = tmp_path / "old.jsonl"
        path.write_text(json.dumps({"role": "user", "content": "one"}) + "\n",
                        encoding="utf-8")
        assert len(Session(path).get_branch()) == 1

    def test_appending_to_a_legacy_file_works(self, tmp_path):
        path = tmp_path / "old.jsonl"
        path.write_text(json.dumps({"role": "user", "content": "one"}) + "\n",
                        encoding="utf-8")
        s = Session(path)
        s.append(UserMessage("two"))
        assert [m.content for m in s.load()] == ["one", "two"]

    def test_a_truncated_final_line_is_survivable(self, tmp_path):
        """The expected shape of a crash: the run died mid-write."""
        path = tmp_path / "torn.jsonl"
        path.write_text(json.dumps({"role": "user", "content": "one"}) + "\n"
                        + '{"role": "assist', encoding="utf-8")
        assert [m.content for m in Session(path).load()] == ["one"]


class TestLinearBehaviour:
    def test_append_then_load(self, session):
        session.append(UserMessage("hello"))
        session.append(AssistantMessage(content="hi"))
        assert [m.content for m in session.load()] == ["hello", "hi"]

    def test_tool_calls_survive_a_round_trip(self, session):
        session.append(AssistantMessage(
            content="", tool_calls=[ToolCall("c1", "bash", {"command": "ls"})]))
        loaded = session.load()[0]
        assert loaded.tool_calls[0].arguments == {"command": "ls"}

    def test_tool_results_survive_a_round_trip(self, session):
        session.append(ToolResultMessage(tool_call_id="c1", content="out",
                                         is_error=True))
        loaded = session.load()[0]
        assert loaded.tool_call_id == "c1" and loaded.is_error


class TestEntryIdentity:
    def test_each_entry_gets_an_id(self, session):
        session.append(UserMessage("one"))
        assert session.get_branch()[0].id

    def test_entries_chain_by_parent(self, session):
        session.append(UserMessage("one"))
        session.append(UserMessage("two"))
        first, second = session.get_branch()
        assert second.parent_id == first.id and first.parent_id is None

    def test_ids_are_unique(self, session):
        for i in range(20):
            session.append(UserMessage(f"m{i}"))
        ids = [e.id for e in session.get_branch()]
        assert len(set(ids)) == len(ids)


class TestBranching:
    def test_forking_from_an_earlier_entry(self, session):
        """The point of the tree: retry from step 2 without losing steps 1-2
        or the branch that went wrong."""
        session.append(UserMessage("one"))
        fork_point = session.append(UserMessage("two"))
        session.append(UserMessage("bad path"))

        session.branch_from(fork_point)
        session.append(UserMessage("better path"))

        assert [m.content for m in session.load()] == ["one", "two", "better path"]

    def test_the_abandoned_branch_is_still_on_disk(self, session):
        session.append(UserMessage("one"))
        fork_point = session.append(UserMessage("two"))
        bad = session.append(UserMessage("bad path"))
        session.branch_from(fork_point)
        session.append(UserMessage("better path"))

        assert [m.content for m in session.load(bad)][-1] == "bad path"

    def test_both_branches_share_their_common_prefix(self, session):
        session.append(UserMessage("one"))
        fork_point = session.append(UserMessage("two"))
        bad = session.append(UserMessage("bad"))
        session.branch_from(fork_point)
        good = session.append(UserMessage("good"))

        assert [e.id for e in session.get_branch(bad)][:2] == \
               [e.id for e in session.get_branch(good)][:2]

    def test_leaves_lists_every_branch_tip(self, session):
        session.append(UserMessage("one"))
        fork_point = session.append(UserMessage("two"))
        session.append(UserMessage("bad"))
        session.branch_from(fork_point)
        session.append(UserMessage("good"))
        assert len(session.leaves()) == 2

    def test_branching_from_an_unknown_id_is_refused(self, session):
        """Silently starting a new root would strand the existing transcript
        somewhere the caller cannot see."""
        session.append(UserMessage("one"))
        with pytest.raises(KeyError):
            session.branch_from("no-such-entry")

    def test_a_fresh_session_has_no_leaves(self, session):
        assert session.leaves() == []


class TestNonMessageEntries:
    def test_a_model_change_is_recorded(self, session):
        session.append(UserMessage("one"))
        session.record_model_change("bigger-model")
        assert any(e.type == "model_change" for e in session.get_branch())

    def test_non_message_entries_are_skipped_by_load(self, session):
        """`load` returns the conversation. A model change is provenance, not
        something to replay at the model."""
        session.append(UserMessage("one"))
        session.record_model_change("bigger-model")
        session.append(UserMessage("two"))
        assert [m.content for m in session.load()] == ["one", "two"]

    def test_a_label_marks_a_point_worth_returning_to(self, session):
        session.append(UserMessage("one"))
        session.record_label("before the risky bit")
        assert any(e.type == "label" and e.payload.get("text") == "before the risky bit"
                   for e in session.get_branch())

    def test_a_compaction_entry_preserves_what_was_dropped(self, session):
        """Compaction stops being destructive: the model sees the summary,
        the branch keeps the original."""
        session.append(UserMessage("one"))
        session.record_compaction(summary="## Goal\nx")
        entry = [e for e in session.get_branch() if e.type == "compaction"][0]
        assert "## Goal" in entry.payload["summary"]


class TestPersistence:
    def test_the_tree_survives_a_reopen(self, tmp_path):
        path = tmp_path / "s.jsonl"
        first = Session(path)
        first.append(UserMessage("one"))
        fork = first.append(UserMessage("two"))
        first.append(UserMessage("bad"))

        reopened = Session(path)
        reopened.branch_from(fork)
        reopened.append(UserMessage("good"))
        assert [m.content for m in reopened.load()] == ["one", "two", "good"]

    def test_reopening_resumes_at_the_newest_leaf(self, tmp_path):
        path = tmp_path / "s.jsonl"
        first = Session(path)
        first.append(UserMessage("one"))
        first.append(UserMessage("two"))

        reopened = Session(path)
        reopened.append(UserMessage("three"))
        assert [m.content for m in reopened.load()] == ["one", "two", "three"]
