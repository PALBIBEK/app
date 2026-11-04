"""Recall ranks a notebook's facts by relevance to the conversation, then by recency."""

from __future__ import annotations

import pytest

from supervisor.memory.client import NotebookClient
from supervisor.memory.cross import recall_across
from supervisor.memory.service import NotebookMemory
from supervisor.memory_service.notebooks.rank import FLOOR, query_weights, ranked_candidates, terms
from supervisor.memory_service.notebooks.types import Fact, Provenance, RecallContext, Sensitivity


def _fact(i: int, text: str) -> Fact:
    return Fact(str(i), text, Provenance(Sensitivity.ORDINARY, ("personal:a",), False), i, i)


def test_terms_drop_stopwords_and_plural_s() -> None:
    assert terms("Alice is allergic to Peanuts, and she loves cats!") == ["alice", "allergic", "peanut", "love", "cat"]
    assert terms("the class pass") == ["class", "pass"], "a double s is not a plural"


def test_the_current_message_counts_double() -> None:
    weights = query_weights("user: tell me about postgres\nassistant: sure\nCurrent message: and redis?")
    # Stemming is crude ("postgres" -> "postgre") but the same on both sides of a match.
    assert weights == {"tell": 1.0, "postgre": 1.0, "sure": 1.0, "redi": 2.0}


def test_without_a_query_or_a_match_recall_is_newest_first() -> None:
    facts = [_fact(1, "first"), _fact(2, "second"), _fact(3, "third")]
    for query in (None, "", "Current message: unrelated words"):
        rows = ranked_candidates(facts, query)
        assert [r.id for r in rows] == ["3", "2", "1"]
        assert {r.score for r in rows} == {FLOOR}


def test_relevant_facts_come_first_then_recency() -> None:
    facts = [_fact(1, "alice is allergic to peanuts"), _fact(2, "deploys on fridays"), _fact(3, "likes peanut butter toast")]
    rows = ranked_candidates(facts, "Current message: can alice eat peanuts?")
    assert [r.id for r in rows] == ["1", "3", "2"], "two matching words beat one; no match is last"
    assert rows[0].score > rows[1].score > rows[2].score == FLOOR
    assert all(FLOOR <= r.score < 1 for r in rows)


@pytest.mark.asyncio
async def test_an_old_relevant_fact_survives_the_recall_budget(notebooks: NotebookClient) -> None:
    nb = await notebooks.ensure(tenant_id="t1", owner="alice", agent_pin="p", name="default")
    await notebooks.capture(nb.id, ["alice is allergic to peanuts"])
    for start in range(0, 400, 200):  # 400 newer short facts: far more than the budget holds
        await notebooks.capture(nb.id, [f"standup note {i} about the sprint board" for i in range(start, start + 200)])
    memory = NotebookMemory(notebooks)
    unasked = await recall_across(memory, [nb.id], RecallContext())
    assert not any("peanuts" in p.text for p in unasked), "newest first, the old fact does not fit"
    picked = await recall_across(memory, [nb.id], RecallContext(query="Current message: can alice eat satay with peanuts?"))
    assert "peanuts" in picked[0].text, "the one fact the question needs leads"
