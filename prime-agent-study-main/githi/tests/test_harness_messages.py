"""Transcript entries that are not LLM messages (port of `harness/messages.ts`).

pi's transcript holds four kinds of entry the provider never sees in their
native form, each converted at the boundary by `convertToLlm`:

* `bashExecution`     — a `!command` the user ran, rendered as text
* `custom`            — an app-defined entry (artifacts, briefings)
* `branchSummary`     — a summary of an abandoned conversation branch
* `compactionSummary` — a summary of history that was compacted away

The distinction between "what happened" (transcript) and "what the model
sees" (context) is the whole point. A memory briefing recorded as a
`UserMessage` would be replayed on resume as though the user had typed it.

**Correcting an earlier mistake.** A first pass had `convert_to_llm` *drop*
custom entries unless they opted in. pi does the opposite: custom entries
become user messages by default, and the opt-*out* is `exclude_from_context`
— which is what pi's `!!command` (run without showing the model) uses.
"""

from __future__ import annotations

from githi.harness.messages import (
    BRANCH_SUMMARY_PREFIX,
    COMPACTION_SUMMARY_PREFIX,
    BashExecutionMessage,
    BranchSummaryMessage,
    CompactionSummaryMessage,
    CustomMessage,
    bash_execution_to_text,
    convert_to_llm,
)
from githi.types import AssistantMessage, ToolResultMessage, UserMessage


class TestPassThrough:
    def test_ordinary_messages_are_unchanged(self):
        messages = [UserMessage("hi"), AssistantMessage(content="hello"),
                    ToolResultMessage(tool_call_id="c1", content="out")]
        assert convert_to_llm(messages) == messages

    def test_ordering_is_preserved(self):
        out = convert_to_llm([UserMessage("first"),
                              CustomMessage(custom_type="note", content="middle"),
                              UserMessage("last")])
        assert [m.content for m in out] == ["first", "middle", "last"]

    def test_the_transcript_is_not_mutated(self):
        transcript = [UserMessage("hi"), CustomMessage(custom_type="n", content="x")]
        convert_to_llm(transcript)
        assert len(transcript) == 2


class TestCustomMessage:
    def test_becomes_a_user_message_by_default(self):
        """pi's actual behaviour: custom entries reach the model."""
        out = convert_to_llm([CustomMessage(custom_type="briefing",
                                            content="recalled facts")])
        assert out[0].to_wire() == {"role": "user", "content": "recalled facts"}

    def test_is_not_a_user_message_in_the_transcript(self):
        """Visible to the model, still distinguishable on disk -- so a resumed
        session does not replay it as something the user typed."""
        message = CustomMessage(custom_type="briefing", content="x")
        assert not isinstance(message, UserMessage)
        assert message.custom_type == "briefing"

    def test_display_flag_is_for_ui_not_the_model(self):
        """`display` controls whether a UI renders it; it does not gate LLM
        visibility. Conflating the two hides content from the model that the
        author only meant to hide from a terminal."""
        out = convert_to_llm([CustomMessage(custom_type="n", content="seen",
                                            display=False)])
        assert len(out) == 1

    def test_details_are_not_sent_to_the_model(self):
        out = convert_to_llm([CustomMessage(custom_type="n", content="body",
                                            details={"secret": "internal"})])
        assert "secret" not in str(out[0].to_wire())


class TestBashExecution:
    def test_renders_command_and_output(self):
        text = bash_execution_to_text(BashExecutionMessage(
            command="ls -la", output="file.txt", exit_code=0))
        assert "ls -la" in text and "file.txt" in text

    def test_no_output_says_so(self):
        """An empty rendering gives the model nothing to reason about."""
        assert "(no output)" in bash_execution_to_text(
            BashExecutionMessage(command="true", output="", exit_code=0))

    def test_nonzero_exit_is_reported(self):
        text = bash_execution_to_text(BashExecutionMessage(
            command="false", output="", exit_code=3))
        assert "3" in text

    def test_zero_exit_is_not_mentioned(self):
        text = bash_execution_to_text(BashExecutionMessage(
            command="true", output="ok", exit_code=0))
        assert "exited with code" not in text

    def test_cancelled_is_reported(self):
        text = bash_execution_to_text(BashExecutionMessage(
            command="sleep 99", output="", exit_code=None, cancelled=True))
        assert "cancelled" in text.lower()

    def test_truncation_names_the_full_output_path(self):
        text = bash_execution_to_text(BashExecutionMessage(
            command="cat big", output="head...", exit_code=0,
            truncated=True, full_output_path="/tmp/out.txt"))
        assert "/tmp/out.txt" in text

    def test_converts_to_a_user_message(self):
        out = convert_to_llm([BashExecutionMessage(command="ls", output="f", exit_code=0)])
        assert out[0].to_wire()["role"] == "user" and "ls" in out[0].to_wire()["content"]

    def test_exclude_from_context_hides_it_from_the_model(self):
        """pi's `!!command`: run it, show the human, do not spend context on
        it. This is the only opt-*out* in the conversion."""
        out = convert_to_llm([BashExecutionMessage(command="ls", output="f",
                                                   exit_code=0,
                                                   exclude_from_context=True)])
        assert out == []


class TestSummaries:
    def test_compaction_summary_is_wrapped_and_labelled(self):
        """The model must know it is reading a summary of removed history,
        not a user turn -- otherwise it treats a condensed recap as a fresh
        instruction."""
        out = convert_to_llm([CompactionSummaryMessage(summary="we did X",
                                                       tokens_before=5000)])
        text = out[0].to_wire()["content"]
        assert "we did X" in text
        assert "compacted" in text.lower() and "<summary>" in text

    def test_branch_summary_is_wrapped_and_labelled(self):
        out = convert_to_llm([BranchSummaryMessage(summary="tried Y", from_id="abc")])
        text = out[0].to_wire()["content"]
        assert "tried Y" in text and "branch" in text.lower()

    def test_prefixes_are_distinct(self):
        """Two different provenances; identical framing would make an
        abandoned branch indistinguishable from compacted main history."""
        assert COMPACTION_SUMMARY_PREFIX != BRANCH_SUMMARY_PREFIX


class TestUnknownEntries:
    def test_an_unconvertible_entry_is_dropped_not_crashed(self):
        """pi's switch falls through to `undefined`. A transcript written by
        a newer version must not break an older reader."""
        class Unknown(CustomMessage):
            def to_llm(self):
                return None

        assert convert_to_llm([Unknown(custom_type="future", content="x")]) == []
