"""Summarizing compaction (port of `harness/compaction/compaction.ts`).

githi's compaction drops the middle of the transcript and leaves a notice
telling the model to "re-derive" anything it needed. That is a real loss: the
agent spent turns and dollars establishing facts, and compaction throws them
away at exactly the moment the task is longest and re-deriving costs most.

pi instead sends the dropped span to the model and keeps a **structured
summary** in its place -- goal, constraints, progress, decisions, next steps.
The summary costs one cheap call and preserves the conclusions rather than the
transcript that produced them, which is the whole point: 40 messages of tool
output usually reduce to five lines of findings.

Two properties matter as much as the summary itself:

* **Incremental update.** A second compaction must fold the new span into the
  *existing* summary. Summarizing a summary loses detail geometrically; a
  long session would end up with a summary of a summary of a summary.
* **Graceful degradation.** The summarizer is a network call and will
  sometimes fail. A failed summary must fall back to the plain notice, never
  crash the run or drop the transcript unsummarized-and-unmarked.
"""

from __future__ import annotations

import pytest

from githi.harness.compaction import (
    SUMMARIZATION_SYSTEM_PROMPT,
    build_summary_request,
    compact,
    extract_previous_summary,
)
from githi.types import (
    AssistantMessage,
    SystemMessage,
    ToolCall,
    ToolResultMessage,
    UserMessage,
)


def _transcript(n: int) -> list:
    messages: list = [SystemMessage("you are githi"), UserMessage("find the commit")]
    for i in range(n):
        call = ToolCall(f"c{i}", "bash", {"command": f"git log -{i}"})
        messages.append(AssistantMessage(content="", tool_calls=[call]))
        messages.append(ToolResultMessage(tool_call_id=f"c{i}", content="x" * 400))
    return messages


class RecordingSummarizer:
    """Stands in for the LLM call so the tests stay offline and fast."""

    def __init__(self, text="## Goal\nfind the commit\n\n## Progress\n- [x] cloned"):
        self.text = text
        self.calls: list[list] = []

    def __call__(self, messages, previous_summary=None):
        self.calls.append(list(messages))
        self.previous = previous_summary
        return self.text


class FailingSummarizer:
    def __call__(self, messages, previous_summary=None):
        raise RuntimeError("provider 503")


class TestSummaryInjection:
    def test_the_summary_reaches_the_transcript(self):
        summarizer = RecordingSummarizer()
        out = compact(_transcript(40), limit_tokens=500, summarizer=summarizer)
        assert any("cloned" in getattr(m, "content", "") for m in out)

    def test_the_summarizer_sees_the_dropped_messages_not_the_kept_ones(self):
        """Summarizing what is being *retained* is pure waste -- those
        messages are still in the context window."""
        summarizer = RecordingSummarizer()
        transcript = _transcript(40)
        compact(transcript, limit_tokens=500, summarizer=summarizer)
        sent = summarizer.calls[0]
        assert len(sent) < len(transcript)

    def test_no_summarizer_falls_back_to_the_plain_notice(self):
        """The pure path still works; summarizing is an enhancement, not a
        precondition for compaction."""
        out = compact(_transcript(40), limit_tokens=500)
        assert any("compacted" in getattr(m, "content", "").lower() for m in out)

    def test_a_failing_summarizer_does_not_break_compaction(self):
        """A provider outage during compaction must not take down a run that
        is already long and expensive."""
        out = compact(_transcript(40), limit_tokens=500, summarizer=FailingSummarizer())
        assert len(out) < len(_transcript(40))
        assert any("compacted" in getattr(m, "content", "").lower() for m in out)

    def test_an_empty_summary_falls_back_too(self):
        out = compact(_transcript(40), limit_tokens=500,
                      summarizer=RecordingSummarizer(text="   "))
        assert any("compacted" in getattr(m, "content", "").lower() for m in out)

    def test_the_summarizer_is_not_called_below_the_threshold(self):
        """Compaction that does not happen must not cost a network call."""
        summarizer = RecordingSummarizer()
        compact(_transcript(2), limit_tokens=100_000, summarizer=summarizer)
        assert summarizer.calls == []


class TestIncrementalUpdate:
    def test_a_previous_summary_is_passed_forward(self):
        """Summarizing a summary loses detail geometrically. The second pass
        must *update* the first, not re-summarize it."""
        summarizer = RecordingSummarizer()
        once = compact(_transcript(40), limit_tokens=500, summarizer=summarizer)
        grown = once + _transcript(40)[2:]
        compact(grown, limit_tokens=500, summarizer=summarizer)
        assert summarizer.previous is not None
        assert "cloned" in summarizer.previous

    def test_extract_finds_an_existing_summary(self):
        messages = [SystemMessage("s"), UserMessage("## Goal\nx"), UserMessage("hi")]
        marked = compact(_transcript(40), limit_tokens=500,
                         summarizer=RecordingSummarizer())
        assert extract_previous_summary(marked) is not None

    def test_extract_returns_none_when_there_is_no_summary(self):
        assert extract_previous_summary(_transcript(4)) is None


class TestSummaryRequest:
    def test_the_system_prompt_forbids_continuing_the_conversation(self):
        """The summarizer is handed a real conversation, so the strongest
        prior is to answer it. It has to be told not to."""
        assert "not continue" in SUMMARIZATION_SYSTEM_PROMPT.lower() \
            or "do not continue" in SUMMARIZATION_SYSTEM_PROMPT.lower()

    def test_the_request_asks_for_the_structured_sections(self):
        request = build_summary_request([UserMessage("hi")])
        text = " ".join(getattr(m, "content", "") for m in request)
        for section in ("## Goal", "## Progress", "## Next Steps"):
            assert section in text

    def test_the_request_demands_exact_identifiers_be_preserved(self):
        """A summary that paraphrases a file path or an error message is
        worse than useless -- the model will act on the paraphrase."""
        request = build_summary_request([UserMessage("hi")])
        text = " ".join(getattr(m, "content", "") for m in request)
        assert "file path" in text.lower()

    def test_an_update_request_carries_the_previous_summary(self):
        request = build_summary_request([UserMessage("hi")],
                                        previous_summary="## Goal\nold goal")
        text = " ".join(getattr(m, "content", "") for m in request)
        assert "old goal" in text and "preserve" in text.lower()


class TestInvariantsStillHold:
    def test_no_orphaned_tool_result(self):
        """The wire-format invariant survives summarization: a tool result
        whose assistant message was dropped is a 400 from the provider."""
        out = compact(_transcript(40), limit_tokens=500,
                      summarizer=RecordingSummarizer())
        call_ids = {c.id for m in out if isinstance(m, AssistantMessage)
                    for c in (m.tool_calls or [])}
        for message in out:
            if isinstance(message, ToolResultMessage):
                assert message.tool_call_id in call_ids

    def test_system_messages_survive(self):
        out = compact(_transcript(40), limit_tokens=500,
                      summarizer=RecordingSummarizer())
        assert any(isinstance(m, SystemMessage) for m in out)

    def test_compaction_actually_shrinks(self):
        transcript = _transcript(40)
        out = compact(transcript, limit_tokens=500, summarizer=RecordingSummarizer())
        assert len(out) < len(transcript)
