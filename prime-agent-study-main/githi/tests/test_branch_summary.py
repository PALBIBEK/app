"""Carrying forward what an abandoned branch learned.

`rewind_to` gives you a second attempt, but today it throws away everything
the first attempt discovered. If the agent spent nine turns establishing that
the API paginates, that the default branch is `trunk`, and that one approach
dead-ends — rewinding discards all of it, and the retry rediscovers the same
walls at the same cost.

That is the opposite of what forking is for. The failed attempt is often the
most informative thing in the session: it is the only place that records what
does *not* work.

So a rewind summarises the branch it abandons and carries the summary onto the
new one. The summary is a `branch_summary` entry, which means it lives in the
session tree like anything else — visible, persisted, and not silently mixed
into the conversation as though the user had said it.

Two things this must not do. It must not summarise the **shared prefix**:
everything before the fork point is still in the transcript, so re-summarising
it wastes tokens describing what the model can already see. And it must not
fail a rewind when the summariser is unavailable — losing the summary is a
degradation, losing the rewind is a broken feature.
"""

from __future__ import annotations

import pytest

from githi.harness.branch_summary import (
    BRANCH_SUMMARY_PREAMBLE,
    build_branch_summary_request,
    summarize_branch,
)
from githi.harness.session import Session
from githi.types import AssistantMessage, ToolCall, ToolResultMessage, UserMessage


@pytest.fixture
def forked(tmp_path):
    """A session that explored one approach, then rewound to try another."""
    session = Session(tmp_path / "s.jsonl")
    session.append(UserMessage("find the commit that broke parsing"))
    session.append(AssistantMessage(content="I'll search the log."))
    fork = session.append(UserMessage("go ahead"))

    # The branch that will be abandoned.
    session.append(AssistantMessage(
        content="", tool_calls=[ToolCall("c1", "bash", {"command": "git log --all"})]))
    session.append(ToolResultMessage(tool_call_id="c1", content="12000 commits"))
    session.append(AssistantMessage(content="Scanning every commit is too slow; "
                                            "the log is 12000 entries."))
    return session, fork


class TestSummarizeBranch:
    def test_it_returns_the_summary_text(self, forked):
        session, fork = forked
        text = summarize_branch(session, fork,
                                summarizer=lambda messages: "## Goal\nfind the commit")
        assert "find the commit" in text

    def test_the_summariser_sees_only_the_abandoned_part(self, forked):
        """The shared prefix is still in the transcript. Re-summarising it
        pays tokens to describe what the model can already read."""
        session, fork = forked
        seen: list = []
        summarize_branch(session, fork,
                         summarizer=lambda messages: (seen.extend(messages), "s")[1])
        text = " ".join(str(getattr(m, "content", "")) for m in seen)
        assert "12000 commits" in text
        assert "find the commit that broke parsing" not in text

    def test_an_empty_branch_summarises_to_nothing(self, tmp_path):
        """Rewinding to the leaf abandons nothing, so there is nothing to
        summarise and no call to pay for."""
        session = Session(tmp_path / "s.jsonl")
        leaf = session.append(UserMessage("only message"))
        calls: list = []
        result = summarize_branch(session, leaf,
                                  summarizer=lambda m: calls.append(1) or "x")
        assert result == "" and calls == []

    def test_a_failing_summariser_returns_empty(self, forked):
        """Losing the summary degrades the rewind; raising would break it."""
        def explode(messages):
            raise RuntimeError("provider down")

        session, fork = forked
        assert summarize_branch(session, fork, summarizer=explode) == ""

    def test_no_summariser_returns_empty(self, forked):
        session, fork = forked
        assert summarize_branch(session, fork, summarizer=None) == ""


class TestRequestShape:
    def test_the_request_asks_for_the_structured_sections(self):
        request = build_branch_summary_request([UserMessage("hi")])
        text = " ".join(str(getattr(m, "content", "")) for m in request)
        assert "## Goal" in text and "## Progress" in text

    def test_it_asks_what_did_not_work(self):
        """The point of keeping a failed branch: the dead ends are the part
        worth carrying, and a summary that omits them invites the retry to
        walk into the same wall."""
        request = build_branch_summary_request([UserMessage("hi")])
        text = " ".join(str(getattr(m, "content", "")) for m in request).lower()
        assert "not work" in text or "dead end" in text or "avoid" in text

    def test_the_preamble_frames_it_as_a_different_branch(self):
        assert "branch" in BRANCH_SUMMARY_PREAMBLE.lower()


class TestSessionIntegration:
    def test_the_summary_is_recorded_as_a_branch_summary_entry(self, forked):
        session, fork = forked
        session.branch_from(fork)
        session.record_branch_summary("## Goal\nthe abandoned attempt")
        assert any(e.type == "branch_summary" for e in session.get_branch())

    def test_it_is_not_replayed_as_a_conversation_message(self, forked):
        """Provenance, not dialogue -- `load` returns what the model said,
        and a summary injected as a user message would read as an instruction."""
        session, fork = forked
        session.branch_from(fork)
        session.record_branch_summary("## Goal\nthe abandoned attempt")
        assert all("abandoned attempt" not in (getattr(m, "content", "") or "")
                   for m in session.load())

    def test_the_abandoned_branch_still_exists(self, forked):
        """Summarising is additive: the original is still there to inspect."""
        session, fork = forked
        session.branch_from(fork)
        session.record_branch_summary("summary")
        session.append(UserMessage("try a different way"))
        assert len(session.leaves()) == 2
