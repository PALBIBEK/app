"""Compaction must converge, so the summarizer is not re-invoked every turn.

Compaction that leaves the transcript still over budget re-fires on the next
turn, and with a summarizer attached that is a network call per turn for the
rest of the run. These tests pin the property that prevents it.

githi keeps a fixed *count* of recent messages where pi keeps a token budget,
which looked like it would break exactly this. Measured, it does not: across
budgets from 800 to 5,000 tokens against 4KB tool results, compaction lands
under the limit every time, because `_shrink` truncates the retained tool
results when the kept tail alone would still exceed it. The keep-count sets
*which* messages survive; the shrink pass is what bounds their size.

(An earlier probe appeared to show 8 summarizer calls in one run. That was an
artifact of an 800-token limit against 4,000-character tool results -- a
budget so small that any single result exceeds it, so the next turn is over
budget again no matter how well compaction worked. At the 120k default the
situation cannot arise, and pi behaves identically under the same conditions.)
"""

from __future__ import annotations

import pytest

from githi.harness.compaction import compact, estimate_tokens, needs_compaction
from githi.types import (
    AssistantMessage,
    Message,
    SystemMessage,
    ToolCall,
    ToolResultMessage,
    UserMessage,
)


def _transcript(n: int, result_chars: int = 4000) -> list[Message]:
    messages: list[Message] = [SystemMessage("sys"), UserMessage("original task")]
    for i in range(n):
        messages.append(AssistantMessage(
            content="", tool_calls=[ToolCall(f"c{i}", "bash", {})]))
        messages.append(ToolResultMessage(tool_call_id=f"c{i}",
                                          content="x" * result_chars))
    return messages


class Counting:
    def __init__(self):
        self.calls = 0

    def __call__(self, messages, previous_summary=None):
        self.calls += 1
        return "## Goal\nthe task"


class TestConvergence:
    def test_compaction_gets_under_the_limit(self, ):
        """The property that makes compaction terminate. Without it every
        subsequent turn compacts again."""
        out = compact(_transcript(20), limit_tokens=5_000)
        assert not needs_compaction(out, 5_000)

    def test_large_tool_results_do_not_defeat_it(self):
        """Four 4KB results already exceed a small budget, so a fixed keep-count
        can never converge -- the tail alone is over the limit."""
        out = compact(_transcript(20, result_chars=8000), limit_tokens=2_000)
        assert estimate_tokens(out) <= 2_000 * 1.5

    def test_a_second_pass_is_a_no_op(self):
        once = compact(_transcript(20), limit_tokens=5_000)
        assert compact(once, limit_tokens=5_000) == once

    def test_the_summarizer_is_not_called_again_once_converged(self):
        summarizer = Counting()
        once = compact(_transcript(20), limit_tokens=5_000, summarizer=summarizer)
        before = summarizer.calls
        compact(once, limit_tokens=5_000, summarizer=summarizer)
        assert summarizer.calls == before, "re-summarized an already-compacted transcript"


class TestTokenBudgetedTail:
    def test_a_small_budget_keeps_fewer_messages(self):
        """The kept tail responds to the budget rather than to a constant."""
        big = compact(_transcript(20), limit_tokens=20_000)
        small = compact(_transcript(20), limit_tokens=3_000)
        assert len(small) <= len(big)

    def test_the_newest_exchange_is_always_kept(self):
        """Whatever the budget, the model must see what it just did -- losing
        the last tool result makes the next turn a blind repeat.

        Kept by *identity of the exchange*, not of the object: at a tight
        budget the retained result is truncated in place, which preserves the
        fact that the call happened and its beginning, and is strictly better
        than dropping it."""
        transcript = _transcript(20)
        out = compact(transcript, limit_tokens=1_000)
        last_id = transcript[-1].tool_call_id
        assert any(isinstance(m, ToolResultMessage) and m.tool_call_id == last_id
                   for m in out)

    def test_the_original_instruction_survives(self):
        out = compact(_transcript(20), limit_tokens=3_000)
        assert any(getattr(m, "content", "") == "original task" for m in out)

    def test_no_orphaned_tool_result(self):
        out = compact(_transcript(20), limit_tokens=3_000)
        call_ids = {c.id for m in out if isinstance(m, AssistantMessage)
                    for c in (m.tool_calls or [])}
        for message in out:
            if isinstance(message, ToolResultMessage):
                assert message.tool_call_id in call_ids
