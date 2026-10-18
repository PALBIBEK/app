"""Context compaction -- the cost lever.

Without it a transcript only grows, so every turn re-sends everything before
it and input cost scales with the square of the turn count. This was measured
on a predecessor harness that skipped compaction: 15 turns of a real task
spent more on re-sending old tool output than on any new reasoning.

The hard requirement is that compaction must never produce a transcript the
provider rejects. An assistant message with tool_calls MUST be followed by a
tool result for each call -- dropping one half of that pair is an immediate
400, and it is the easy mistake to make when trimming from the front.
"""

from __future__ import annotations

import pytest

from githi.harness.compaction import compact, estimate_tokens, needs_compaction
from githi.types import AssistantMessage, SystemMessage, ToolCall, ToolResultMessage, UserMessage


def _pair(i: int, size: int = 100) -> list:
    call = ToolCall(id=f"c{i}", name="bash", arguments={"command": "x"})
    return [AssistantMessage(content="", tool_calls=[call]),
            ToolResultMessage(tool_call_id=f"c{i}", content="y" * size)]


def _transcript(pairs: int) -> list:
    msgs = [UserMessage("do the thing")]
    for i in range(pairs):
        msgs += _pair(i)
    return msgs


class TestEstimate:
    def test_grows_with_content(self):
        assert estimate_tokens([UserMessage("x" * 400)]) > estimate_tokens([UserMessage("x")])

    def test_counts_tool_results(self):
        """Tool output is usually the bulk of a transcript; missing it would
        make the estimate useless precisely when it matters."""
        assert estimate_tokens(_pair(0, 4000)) > 500

    def test_empty_transcript(self):
        assert estimate_tokens([]) == 0


class TestThreshold:
    def test_small_transcript_is_left_alone(self):
        assert needs_compaction(_transcript(1), limit_tokens=100_000) is False

    def test_large_transcript_triggers(self):
        assert needs_compaction(_transcript(200), limit_tokens=1_000) is True


class TestCompact:
    def test_reduces_size(self):
        msgs = _transcript(40)
        out = compact(msgs, limit_tokens=1_000)
        assert estimate_tokens(out) < estimate_tokens(msgs)

    def test_keeps_the_first_user_message(self):
        """The original instruction is the one message that must never be
        dropped -- losing it means the agent forgets what it was asked."""
        out = compact(_transcript(40), limit_tokens=500)
        assert any(isinstance(m, UserMessage) and "do the thing" in m.content for m in out)

    def test_keeps_the_most_recent_exchanges(self):
        msgs = _transcript(40)
        msgs.append(UserMessage("LATEST"))
        out = compact(msgs, limit_tokens=500)
        assert out[-1].content == "LATEST"

    def test_inserts_a_summary_marker(self):
        """The model must be told history was dropped, or it will confidently
        reason about tool output it can no longer see."""
        out = compact(_transcript(40), limit_tokens=500)
        assert any("compact" in m.content.lower() or "elided" in m.content.lower()
                   for m in out if isinstance(m, (UserMessage, SystemMessage)))

    def test_never_orphans_a_tool_result(self):
        """The provider rejects a tool result whose assistant tool_call is
        missing. This is the failure trimming-from-the-front causes."""
        out = compact(_transcript(40), limit_tokens=500)
        call_ids = {c.id for m in out if isinstance(m, AssistantMessage) for c in m.tool_calls}
        for m in out:
            if isinstance(m, ToolResultMessage):
                assert m.tool_call_id in call_ids

    def test_never_orphans_a_tool_call(self):
        """The mirror image: an assistant message requesting a tool with no
        result following it is equally invalid."""
        out = compact(_transcript(40), limit_tokens=500)
        result_ids = {m.tool_call_id for m in out if isinstance(m, ToolResultMessage)}
        for m in out:
            if isinstance(m, AssistantMessage) and m.tool_calls:
                for call in m.tool_calls:
                    assert call.id in result_ids

    def test_below_threshold_is_returned_unchanged(self):
        msgs = _transcript(1)
        assert compact(msgs, limit_tokens=100_000) == msgs

    def test_idempotent(self):
        """Compacting an already-compacted transcript must converge, not
        shrink it away turn after turn."""
        once = compact(_transcript(40), limit_tokens=500)
        assert compact(once, limit_tokens=500) == once


def test_system_messages_are_preserved():
    msgs = [SystemMessage("rules")] + _transcript(40)
    out = compact(msgs, limit_tokens=500)
    assert isinstance(out[0], SystemMessage)
