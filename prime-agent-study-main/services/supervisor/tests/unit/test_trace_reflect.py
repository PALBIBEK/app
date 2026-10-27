"""Reflecting proposes a prompt change from how runs went wrong, and changes nothing itself."""

from __future__ import annotations

import json
from typing import Any

import pytest

from supervisor.seams.providers.gateway.fake_gateway import FakeModelGatewayClient
from supervisor.traces.reflect import ProposalError, evidence_text, parse_proposal, reflect_messages
from tests.support.trace_api import ALICE, BOB, Api

SECTIONS = [{"id": "role", "text": "You fix code."}, {"id": "tools", "text": "Use bash."}]


def test_a_reply_becomes_a_proposal_that_never_loses_a_section() -> None:
    reply = "Here: " + json.dumps({
        "rationale": "Runs repeat make; say to stop after a failure.",
        "persona": "Coder",
        "sections": [{"id": "tools", "text": "Use bash. After a command fails twice, stop and explain."},
                     {"id": "verify", "text": "Run the tests after every change."},
                     {"id": "bad id!", "text": "dropped"}],
    })
    p = parse_proposal(reply, "Coder", SECTIONS)
    assert [s.id for s in p.sections] == ["role", "tools", "verify"]  # role was left out: kept as it was
    assert p.sections[0].text == "You fix code."
    assert p.changed == ["tools", "verify"]
    assert p.rationale.startswith("Runs repeat make")


def test_a_reply_that_is_not_a_proposal_is_refused_not_guessed() -> None:
    with pytest.raises(ProposalError):
        parse_proposal("I would change the tools section.", "Coder", SECTIONS)
    unchanged = parse_proposal(json.dumps({"rationale": "nothing to fix", "sections": []}), "Coder", SECTIONS)
    assert unchanged.changed == [] and [s.text for s in unchanged.sections] == ["You fix code.", "Use bash."]


def test_the_model_reads_the_prompt_and_the_evidence_and_nothing_else() -> None:
    issues = [{"title": "bash calls failed", "runs": 2,
               "examples": [{"input": "fix the build", "summary": "Ran 3 commands (3 failed).", "reason": "exit 2"}]}]
    text = evidence_text(issues)
    assert "Issue 1: bash calls failed (2 runs)" in text and "why it failed: exit 2" in text
    messages = reflect_messages("Coder", SECTIONS, issues, notes="keep answers short")
    assert "Use bash." in messages[1].content and "keep answers short" in messages[1].content


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
    model = Recording(json.dumps({"rationale": "bash failed; check output.", "sections": [
        {"id": "tools", "text": "Use bash; read the error before running it again."}]}))
    monkeypatch.setattr(type(api.state.manager), "session_model_client", lambda self, s, row: model)
    r = await api.client.post("/v1/traces/reflect", json=body, headers=ALICE)
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["proposal"]["changed"] == ["tools"]
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
