"""Reflecting proposes a prompt change from how runs went wrong, and changes nothing itself."""

from __future__ import annotations

import json
from typing import Any

import pytest

from supervisor.seams.providers.gateway.fake_gateway import FakeModelGatewayClient
from supervisor.traces.reflect import (
    MAX_EDITS,
    MAX_HISTORY,
    ProposalError,
    evidence_text,
    history_text,
    parse_proposal,
    reflect_messages,
)
from tests.support.trace_api import ALICE, BOB, Api

SECTIONS = [{"id": "role", "text": "You fix code."}, {"id": "tools", "text": "Use bash."}]


def test_edits_apply_in_order_and_one_that_cannot_apply_is_skipped_with_why() -> None:
    """Itemized edits (ACE's ADD operations, Prime Agent's small /refine edits), never a rewrite."""
    reply = "Here: " + json.dumps({
        "rationale": "Runs repeat make; say to stop after a failure.",
        "expected_outcome": "fewer repeated bash calls",
        "edits": [
            {"action": "add", "section": "tools", "text": "After a command fails twice, stop and explain.",
             "reason": "bash calls repeated"},
            {"action": "add", "section": "verify", "text": "Run the tests after every change."},
            {"action": "replace", "section": "role", "old": "fix code", "new": "fix code, carefully"},
            {"action": "replace", "section": "tools", "old": "not there", "new": "x"},
            {"action": "replace", "section": "role", "old": "carefully", "new": ""},
            {"action": "delete", "section": "role"},
            {"action": "add", "section": "bad id!", "text": "dropped"},
        ],
    })
    p = parse_proposal(reply, "Coder", SECTIONS)
    assert [(s.id, s.text) for s in p.sections] == [
        ("role", "You fix code, carefully."),
        ("tools", "Use bash.\nAfter a command fails twice, stop and explain."),
        ("verify", "Run the tests after every change."),
    ]
    assert p.changed == ["role", "tools", "verify"]
    assert [(e.action, e.section) for e in p.edits] == [("add", "tools"), ("add", "verify"), ("replace", "role")]
    assert p.edits[0].reason == "bash calls repeated" and p.edits[2].old == "fix code"
    assert p.skipped == [
        "edit 4: the passage to replace is not in 'tools' exactly once",
        "edit 5: no new text for the passage (edits never delete)",
        "edit 6: unknown action 'delete'",
        "edit 7: 'bad id!' is not a section id",
    ]
    assert p.persona == "Coder" and p.expected_outcome == "fewer repeated bash calls"


def test_a_proposal_makes_at_most_a_few_edits() -> None:
    edits = [{"action": "add", "section": "tools", "text": f"rule {n}"} for n in range(MAX_EDITS + 2)]
    p = parse_proposal(json.dumps({"rationale": "r", "edits": edits}), "Coder", SECTIONS)
    assert len(p.edits) == MAX_EDITS and len(p.skipped) == 2


def test_a_reply_that_is_not_a_proposal_is_refused_not_guessed() -> None:
    with pytest.raises(ProposalError):
        parse_proposal("I would change the tools section.", "Coder", SECTIONS)
    with pytest.raises(ProposalError):  # a whole rewritten prompt is not edits
        parse_proposal(json.dumps({"rationale": "r", "sections": [{"id": "role", "text": "x"}]}), "Coder", SECTIONS)
    unchanged = parse_proposal(json.dumps({"rationale": "nothing to fix", "edits": []}), "Coder", SECTIONS)
    assert unchanged.changed == [] and [s.text for s in unchanged.sections] == ["You fix code.", "Use bash."]


def test_the_model_reads_the_prompt_the_evidence_and_what_became_of_earlier_changes() -> None:
    issues = [{"title": "bash calls failed", "runs": 2,
               "examples": [{"input": "fix the build", "summary": "Ran 3 commands (3 failed).", "reason": "exit 2"}]}]
    text = evidence_text(issues)
    assert "Issue 1: bash calls failed (2 runs)" in text and "why it failed: exit 2" in text
    history = [{"outcome": "refused", "rationale": "retry more", "why": ["it did worse on 1 of 2 saved runs"],
                "edits": [{"action": "add", "section": "tools", "text": "Retry failed commands."}]}]
    messages = reflect_messages("Coder", SECTIONS, issues, notes="keep answers short", history=history)
    asked = messages[1].content
    assert "Use bash." in asked and "keep answers short" in asked
    assert "Change 1: refused" in asked and "add in tools: Retry failed commands." in asked
    assert "what came of it: it did worse on 1 of 2 saved runs" in asked
    assert history_text([]) == "No change was proposed before."
    many = history_text([{"outcome": f"o{n}"} for n in range(MAX_HISTORY + 5)])
    assert many.count("Change ") == MAX_HISTORY and "o5" in many and "o4\n" not in many


class Recording(FakeModelGatewayClient):
    def __init__(self, reply: str) -> None:
        super().__init__(assistant_text=reply)
        self.asked: list[Any] = []

    def complete(self, messages, tools, *, model=None, tool_choice=None):  # type: ignore[no-untyped-def]
        self.asked.append(messages)
        return super().complete(messages, tools, model=model, tool_choice=tool_choice)


async def test_reflecting_on_failing_runs_proposes_a_change(api: Api, monkeypatch: pytest.MonkeyPatch) -> None:
    body = {"composition_id": "githi-v1", "persona": "Coder", "sections": SECTIONS}
    sid = await api.session()
    await api.run(sid, "r1", "fix the build", start=1_000)
    await api.client.get(f"/v1/sessions/{sid}/traces", headers=ALICE)  # rollups recorded
    r = await api.client.post("/v1/traces/reflect", json=body, headers=ALICE)
    assert r.status_code == 409 and r.json()["detail"]["error_code"] == "NO_EVIDENCE"  # nothing went wrong

    await api.run(sid, "r2", "fix the tests", start=5_000, tool_ok=False)
    await api.client.get(f"/v1/sessions/{sid}/traces", headers=ALICE)
    model = Recording(json.dumps({"rationale": "bash failed; check output.", "edits": [
        {"action": "add", "section": "tools", "text": "Read the error before running it again."}]}))
    monkeypatch.setattr(type(api.state.manager), "session_model_client", lambda self, s, row: model)
    past = [{"outcome": "rejected", "rationale": "shorter answers", "why": ["a person rejected it"]}]
    r = await api.client.post("/v1/traces/reflect", json=body | {"history": past}, headers=ALICE)
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["proposal"]["changed"] == ["tools"]
    assert out["proposal"]["edits"][0]["text"] == "Read the error before running it again."
    assert "Change 1: rejected" in model.asked[0][1].content
    assert [e["title"] for e in out["evidence"]] == ["bash calls failed"]
    assert out["evidence"][0]["examples"][0]["run_id"] == "r2"
    assert "fix the tests" in model.asked[0][1].content

    # an experiment's replays are never evidence: the optimizer must not see what grades it
    r = await api.client.post("/v1/traces/reflect", json=body | {"exclude_sessions": [sid]}, headers=ALICE)
    assert r.status_code == 409

    # Bob sees none of Alice's runs, so he has nothing to learn from
    r = await api.client.post("/v1/traces/reflect", json=body, headers=BOB)
    assert r.status_code == 409


async def test_a_reply_that_is_not_a_proposal_is_refused_with_why(api: Api, monkeypatch: pytest.MonkeyPatch) -> None:
    sid = await api.session()
    await api.run(sid, "r1", "fix it", start=1_000, tool_ok=False)
    await api.client.get(f"/v1/sessions/{sid}/traces", headers=ALICE)
    monkeypatch.setattr(type(api.state.manager), "session_model_client",
                        lambda self, s, row: FakeModelGatewayClient(assistant_text="no idea"))
    r = await api.client.post("/v1/traces/reflect", json={"composition_id": "githi-v1", "sections": SECTIONS},
                              headers=ALICE)
    assert r.status_code == 422 and r.json()["detail"]["error_code"] == "NOT_A_PROPOSAL"
