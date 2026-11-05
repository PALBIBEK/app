"""Reflecting proposes a prompt change from how runs went wrong, and changes nothing itself."""

from __future__ import annotations

import json
from typing import Any

import pytest

from supervisor.seams.providers.gateway.fake_gateway import FakeModelGatewayClient
from supervisor.traces.reflect import (
    MAX_EDITS,
    MAX_HISTORY,
    MAX_NOTES,
    TRAJECTORIES,
    ProposalError,
    evidence_text,
    history_text,
    parse_proposal,
    reflect_messages,
    trajectories_text,
    trajectory_record,
)
from tests.support.trace_api import ALICE, BOB, Api

SECTIONS = [{"id": "role", "text": "You fix code."}, {"id": "tools", "text": "Use bash."}]


def test_edits_change_only_the_learnt_notes_and_one_that_cannot_apply_is_skipped_with_why() -> None:
    """Prime Agent's /refine: the base prompt is immutable, notes are created, updated and
    deleted by id; ids are ACE's next global id."""
    learned = [{"id": "note-00001", "text": "Read the error first."}, {"id": "note-00004", "text": "Retry twice."}]
    reply = "Here: " + json.dumps({
        "rationale": "Retrying hurt; say to stop after a failure.",
        "expected_outcome": "fewer repeated bash calls",
        "edits": [
            {"action": "delete", "id": "note-00004", "reason": "retrying repeated the failure"},
            {"action": "add", "text": "After a command fails twice, stop and explain.", "reason": "bash calls repeated"},
            {"action": "update", "id": "note-00001", "text": "Read the whole error first."},
            {"action": "update", "id": "note-00009", "text": "x"},
            {"action": "update", "id": "note-00001", "text": ""},
            {"action": "add", "text": "Read the whole error first."},
            {"action": "replace", "section": "role", "old": "fix code", "new": "anything"},
        ],
    })
    p = parse_proposal(reply, "Coder", SECTIONS, learned)
    assert [(s.id, s.text) for s in p.sections] == [(s["id"], s["text"]) for s in SECTIONS], "the person's: unchanged"
    assert [(n.id, n.text) for n in p.learned] == [
        ("note-00001", "Read the whole error first."),
        ("note-00005", "After a command fails twice, stop and explain."),
    ]
    assert p.changed == ["note-00004", "note-00005", "note-00001"]
    assert [(e.action, e.id) for e in p.edits] == [("delete", "note-00004"), ("add", "note-00005"),
                                                   ("update", "note-00001")]
    assert p.edits[0].text == "Retry twice." and p.edits[2].old == "Read the error first."
    assert p.skipped == [
        "edit 4: there is no note 'note-00009'",
        "edit 5: no new text for note-00001 (to remove it, delete it)",
        "edit 6: that note is already kept",
        "edit 7: unknown action 'replace'",
    ]
    assert p.persona == "Coder" and p.expected_outcome == "fewer repeated bash calls"


def test_a_proposal_makes_at_most_a_few_edits_and_the_agent_keeps_at_most_so_many_notes() -> None:
    edits = [{"action": "add", "text": f"rule {n}"} for n in range(MAX_EDITS + 2)]
    p = parse_proposal(json.dumps({"rationale": "r", "edits": edits}), "Coder", SECTIONS)
    assert len(p.edits) == MAX_EDITS and len(p.skipped) == 2
    assert [n.id for n in p.learned][:2] == ["note-00001", "note-00002"]
    full = [{"id": f"note-{n:05d}", "text": f"kept {n}"} for n in range(1, MAX_NOTES + 1)]
    p = parse_proposal(json.dumps({"rationale": "r", "edits": [{"action": "add", "text": "one more"}]}),
                       "Coder", SECTIONS, full)
    assert p.edits == [] and p.skipped[0].startswith(f"edit 1: the agent keeps at most {MAX_NOTES} notes")


def test_a_reply_that_is_not_a_proposal_is_refused_not_guessed() -> None:
    with pytest.raises(ProposalError):
        parse_proposal("I would change the tools section.", "Coder", SECTIONS)
    with pytest.raises(ProposalError):  # a whole rewritten prompt is not edits
        parse_proposal(json.dumps({"rationale": "r", "sections": [{"id": "role", "text": "x"}]}), "Coder", SECTIONS)
    unchanged = parse_proposal(json.dumps({"rationale": "nothing to fix", "edits": []}), "Coder", SECTIONS)
    assert unchanged.changed == [] and unchanged.learned == []


def test_the_model_reads_the_prompt_the_evidence_and_what_became_of_earlier_changes() -> None:
    issues = [{"title": "bash calls failed", "runs": 2,
               "examples": [{"input": "fix the build", "summary": "Ran 3 commands (3 failed).", "reason": "exit 2"}]}]
    text = evidence_text(issues)
    assert "Issue 1: bash calls failed (2 runs)" in text and "why it failed: exit 2" in text
    history = [{"outcome": "refused", "rationale": "retry more", "why": ["it did worse on 1 of 2 saved runs"],
                "edits": [{"action": "add", "id": "note-00002", "text": "Retry failed commands."}]}]
    messages = reflect_messages("Coder", SECTIONS, issues, notes="keep answers short", history=history,
                                learned=[{"id": "note-00001", "text": "Read the error first."}])
    asked = messages[1].content
    assert "Use bash." in asked and "keep answers short" in asked and "note-00001" in asked
    assert "Change 1: refused" in asked and "add note-00002: Retry failed commands." in asked
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
        {"action": "add", "text": "Read the error before running it again."}]}))
    monkeypatch.setattr(type(api.state.manager), "session_model_client", lambda self, s, row: model)
    past = [{"outcome": "rejected", "rationale": "shorter answers", "why": ["a person rejected it"]}]
    r = await api.client.post("/v1/traces/reflect", json=body | {"history": past}, headers=ALICE)
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["proposal"]["changed"] == ["note-00001"]
    assert out["proposal"]["learned"] == [{"id": "note-00001", "text": "Read the error before running it again."}]
    assert out["proposal"]["sections"] == SECTIONS
    assert "Change 1: rejected" in model.asked[0][1].content
    # the failing run is read in full: GEPA's reflective record, steps and why it failed
    asked = model.asked[0][1].content
    assert out["read_in_full"] == [{"session_id": sid, "run_id": "r2"}]
    assert "Run 1:\n[Inputs]\nfix the tests\n[Generated Outputs]\n" in asked
    assert "bash" in asked.split("[Generated Outputs]")[1] and "[Feedback]\ncheck tool_errors" in asked
    assert [e["title"] for e in out["evidence"]] == ["bash calls failed"]
    assert out["evidence"][0]["examples"][0]["run_id"] == "r2"
    assert "fix the tests" in model.asked[0][1].content

    # a change for one issue reads it first, wherever it ranks
    await api.client.put(f"/v1/sessions/{sid}/runs/r1/feedback", json={"good": False}, headers=ALICE)
    ranked = (await api.client.get("/v1/traces/issues", headers=ALICE)).json()["issues"]
    assert [i["title"] for i in ranked][:2] == ["bash calls failed", "Marked wrong by people"]
    focused = await api.client.post("/v1/traces/reflect", json=body | {"focus_issue": ranked[1]["key"]}, headers=ALICE)
    assert focused.status_code == 200, focused.text
    assert [e["title"] for e in focused.json()["evidence"]][:2] == ["Marked wrong by people", "bash calls failed"]

    # an experiment's replays are never evidence: the optimizer must not see what grades it
    r = await api.client.post("/v1/traces/reflect", json=body | {"exclude_sessions": [sid]}, headers=ALICE)
    assert r.status_code == 409

    # Bob sees none of Alice's runs, so he has nothing to learn from
    r = await api.client.post("/v1/traces/reflect", json=body, headers=BOB)
    assert r.status_code == 409

    # a replay is never evidence even when the caller's list does not name it (a list can be cut
    # short): the supervisor marks replays itself
    marked = await api.client.put(f"/v1/sessions/{sid}/evaluation", json={"experiment_id": "e"}, headers=ALICE)
    assert marked.status_code == 204
    r = await api.client.post("/v1/traces/reflect", json=body, headers=ALICE)
    assert r.status_code == 409 and r.json()["detail"]["error_code"] == "NO_EVIDENCE"


async def test_a_reply_that_is_not_a_proposal_is_refused_with_why(api: Api, monkeypatch: pytest.MonkeyPatch) -> None:
    sid = await api.session()
    await api.run(sid, "r1", "fix it", start=1_000, tool_ok=False)
    await api.client.get(f"/v1/sessions/{sid}/traces", headers=ALICE)
    monkeypatch.setattr(type(api.state.manager), "session_model_client",
                        lambda self, s, row: FakeModelGatewayClient(assistant_text="no idea"))
    r = await api.client.post("/v1/traces/reflect", json={"composition_id": "githi-v1", "sections": SECTIONS},
                              headers=ALICE)
    assert r.status_code == 422 and r.json()["detail"]["error_code"] == "NOT_A_PROPOSAL"


def test_a_reflection_reads_at_most_a_minibatch_of_runs_in_full() -> None:
    records = [trajectory_record(f"ask {n}", "step", ["check x: failed"]) for n in range(TRAJECTORIES + 2)]
    text = trajectories_text(records)
    assert text.count("[Inputs]") == TRAJECTORIES and "ask 0" in text and f"ask {TRAJECTORIES}" not in text
    assert trajectory_record(None, "s", [])["Feedback"] == "judged a failure"
    assert trajectories_text([]) == "None read."
