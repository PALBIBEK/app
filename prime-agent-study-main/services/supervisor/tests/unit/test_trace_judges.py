"""Summaries, judges and issues: a run in one sentence, a rubric applied by a model, and the
failures grouped into issues."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from supervisor.agent.events import (
    ApprovalRequired,
    ApprovalResolved,
    ErrorEvent,
    RunEnd,
    RunStart,
    TurnEnd,
    TurnStart,
)
from supervisor.db.models import RunTraceRow, SessionRow, TraceScoreRow
from supervisor.journal.reader import JournalReader
from supervisor.seams.providers.gateway.fake_gateway import FakeModelGatewayClient
from supervisor.traces.after_run import after_run
from supervisor.traces.judges import (
    JUDGE_MODEL_ENV,
    Judge,
    judge_run,
    kappa,
    parse_verdict,
    sampled,
    save_judge,
)
from supervisor.traces.render import run_text
from supervisor.traces.store import record_run
from supervisor.traces.summary import SUMMARY_MODEL_ENV, describe
from tests.support.trace_api import ALICE, BOB, Api, list_traces
from tests.support.trace_journal import Journal, model, ran, tool, user


def _summary(j: Journal) -> str:
    return describe(j.trace())


def test_a_run_reads_as_one_sentence() -> None:
    j = Journal().at(0, RunStart()).at(0, TurnStart())
    tool(j, 10, "c1", "read", {"file_path": "a.py"}, ran("c1", "read", "x\n"))
    tool(j, 20, "c2", "edit", {"file_path": "a.py", "old_text": "x", "new_text": "y"}, ran("c2", "edit", {}))
    tool(j, 30, "c3", "bash", {"command": "pytest"}, ran("c3", "bash", {"exit_code": 1, "stdout": ""}))
    tool(j, 40, "c4", "bash", {"command": "pytest"}, ran("c4", "bash", {"exit_code": 0, "stdout": ""}))
    j.at(50, model("Fixed.")).at(60, TurnEnd("stop")).at(60, RunEnd(messages=[]))
    assert _summary(j) == "Ran 2 commands (1 failed), changed a.py, looked at 1 thing; answered."

    failed = (Journal().at(0, RunStart()).at(0, TurnStart()).at(5, ErrorEvent("GatewayError", False, "down"))
              .at(9, TurnEnd("error")).at(9, RunEnd(messages=[])))
    assert _summary(failed) == "Failed: GatewayError: down."

    denied = Journal().at(0, RunStart()).at(0, TurnStart())
    denied.at(1, ApprovalRequired("a", "ok?", ["allow", "deny"], 5, metadata={"tool": "bash", "tool_call_id": "c"}))
    denied.at(2, ApprovalResolved("a", "deny", "denied"))
    tool(denied, 3, "c", "bash", {"command": "rm -rf /"}, ran("c", "bash", ok=False, code="APPROVAL_DENIED", error="denied"))
    denied.at(9, TurnEnd("stop")).at(9, RunEnd(messages=[]))
    assert _summary(denied) == "Was denied 1 call; answered."


def test_run_text_shows_the_run_in_reading_order_and_cuts_the_middle_when_long() -> None:
    j = Journal().at(0, RunStart()).at(0, user("fix the test")).at(0, TurnStart())
    tool(j, 10, "c1", "bash", {"command": "pytest"}, ran("c1", "bash", {"exit_code": 1, "stdout": "1 failed"}))
    j.at(20, model("It fails on dates.")).at(30, TurnEnd("stop")).at(30, RunEnd(messages=[]))
    text = run_text(j.trace())
    assert text.splitlines() == [
        "USER: fix the test",
        'TOOL CALL bash {"command": "pytest"}',
        "  RESULT (exit 1): 1 failed",
        "AGENT: It fails on dates.",
        "ENDED: ok",
    ]
    long = run_text(j.trace(), limit=40)
    assert "left out" in long and long.startswith("USER: fix the test"[:20]) and long.endswith("ENDED: ok")


def test_a_reply_is_a_verdict_or_it_is_unclear() -> None:
    assert parse_verdict('{"verdict": "PASS", "reason": "answers it"}').label == "pass"
    assert parse_verdict('Sure.\n```json\n{"verdict": "fail", "reason": "wrong file"}\n```').reason == "wrong file"
    unclear = parse_verdict("echo:the run was fine")
    assert unclear.label == "unclear" and unclear.reason == "the judge's reply was not a verdict: echo:the run was fine"
    assert parse_verdict('{"verdict": "maybe"}').label == "unclear"


def test_sampling_is_a_fixed_share_and_the_same_every_time() -> None:
    judge = Judge("j1", "c", "n", "rubric text", 0.25, None, 1, True)
    picks = [sampled(judge, f"run-{i}") for i in range(2000)]
    assert 400 < sum(picks) < 600
    assert picks == [sampled(judge, f"run-{i}") for i in range(2000)]
    assert sampled(Judge("j", "c", "n", "r", 1.0, None, 1, True), "x")
    assert not sampled(Judge("j", "c", "n", "r", 0.0, None, 1, True), "x")


async def _finished_run(api: Api) -> tuple[SessionRow, str]:
    sid = await api.session()
    await api.run(sid, "r1", "fix it", start=1_000)
    async with api.factory() as db:
        row = await db.get(SessionRow, sid)
    assert row is not None
    return row, sid


async def test_judges_give_verdicts_kept_with_their_version(api: Api) -> None:
    row, sid = await _finished_run(api)
    reader = JournalReader(api.factory)
    j1 = await save_judge(api.factory, tenant_id="t1", composition_id="githi-v1", name="answers",
                          rubric="Answers the question that was asked.", sample_rate=1.0, model=None,
                          enabled=True, created_by="alice")
    await save_judge(api.factory, tenant_id="t1", composition_id="githi-v1", name="off", rubric="Never runs.",
                     sample_rate=1.0, model=None, enabled=False, created_by="alice")
    trace = await after_run(api.factory, reader, sid, "r1",
                            lambda s: FakeModelGatewayClient(assistant_text='{"verdict": "fail", "reason": "it guessed"}'))
    assert trace is not None
    async with api.factory() as db:
        scores = (await db.execute(select(TraceScoreRow).where(TraceScoreRow.source == "judge"))).scalars().all()
    assert [(s.name, s.label, s.reason, s.evaluator) for s in scores] == [("answers", "fail", "it guessed", "judge:answers@v1")]

    # A changed rubric is a new version; judging again replaces the earlier verdict.
    j2 = await save_judge(api.factory, tenant_id="t1", composition_id="githi-v1", name="answers",
                          rubric="Answers the question, and says what it changed.", sample_rate=1.0, model=None,
                          enabled=True, created_by="alice")
    assert (j1["id"], j1["version"], j2["version"]) == (j2["id"], 1, 2)
    given = await judge_run(api.factory, row, trace, FakeModelGatewayClient(assistant_text="no idea"))
    assert [g.label for g in given] == ["unclear"]
    async with api.factory() as db:
        scores = (await db.execute(select(TraceScoreRow).where(TraceScoreRow.source == "judge"))).scalars().all()
    assert [(s.label, s.evaluator) for s in scores] == [("unclear", "judge:answers@v2")]
    assert await judge_run(api.factory, row, trace, None) == []  # no client: nobody asked


async def test_a_model_can_write_the_summary_and_it_outlives_rebuilds(api: Api, monkeypatch: pytest.MonkeyPatch) -> None:
    _, sid = await _finished_run(api)
    reader = JournalReader(api.factory)
    await after_run(api.factory, reader, sid, "r1", lambda s: FakeModelGatewayClient(assistant_text="ignored"))
    async with api.factory() as db:
        row = await db.get(RunTraceRow, (sid, "r1"))
        assert row is not None and row.summary_by == "rules" and row.summary == "Ran 1 command; answered."

    monkeypatch.setenv(SUMMARY_MODEL_ENV, "small-model")
    seen: list[str | None] = []

    class Recording(FakeModelGatewayClient):
        def complete(self, messages, tools, *, model=None, tool_choice=None):  # type: ignore[no-untyped-def]
            seen.append(model)
            return super().complete(messages, tools, model=model, tool_choice=tool_choice)

    await after_run(api.factory, reader, sid, "r1",
                    lambda s: Recording(assistant_text="Asked to fix it; ran make once and answered."))
    listed = (await api.client.get(f"/v1/sessions/{sid}/traces", headers=ALICE)).json()["items"]
    assert listed[0]["summary"] == "Asked to fix it; ran make once and answered." and seen == ["small-model"]


async def test_failures_group_into_issues_with_examples_and_versions(api: Api) -> None:
    sid = await api.session()
    for i in range(3):
        await api.run(sid, f"broke-{i}", f"attempt {i}", start=1_000 * (i + 1), tool_ok=False)
    await api.run(sid, "fine", "works", start=9_000)
    await list_traces(api, ALICE)  # fills in the rollups and their checks
    await api.client.put(f"/v1/sessions/{sid}/runs/fine/feedback", json={"good": False, "comment": "too slow"},
                         headers=ALICE)

    r = await api.client.get("/v1/traces/issues", params={"composition_id": "githi-v1"}, headers=ALICE)
    assert r.status_code == 200, r.text
    issues = r.json()["issues"]
    assert [(i["title"], i["runs"]) for i in issues] == [("bash calls failed", 3), ("Marked wrong by people", 1)]
    first = issues[0]
    assert [e["run_id"] for e in first["examples"]] == ["broke-2", "broke-1", "broke-0"]
    assert first["versions"] == [{"composition_digest": "sha256:" + "a" * 64, "runs": 3}]
    assert issues[1]["examples"][0]["reason"] == "too slow"
    assert (await api.client.get("/v1/traces/issues", headers=BOB)).json()["issues"] == []


async def test_an_issue_lists_all_its_runs_but_not_an_experiments_replays(api: Api) -> None:
    """Saving an issue to a dataset takes its runs, every one (not the five examples), and none
    that an experiment replayed: a replay is a measurement, not a failure to learn from."""
    sid, replay = await api.session(), await api.session()
    for i in range(7):
        await api.run(sid, f"broke-{i}", f"attempt {i}", start=1_000 * (i + 1), tool_ok=False)
    await api.run(replay, "again", "attempt 0", start=20_000, tool_ok=False)
    await list_traces(api, ALICE)
    marked = await api.client.put(f"/v1/sessions/{replay}/evaluation", json={"experiment_id": "exp-1"}, headers=ALICE)
    assert marked.status_code == 204

    listed = (await api.client.get("/v1/traces/issues", headers=ALICE)).json()["issues"]
    assert (listed[0]["runs"], len(listed[0]["examples"])) == (8, 5), "the issues page still counts every run"
    live = (await api.client.get("/v1/traces/issues", params={"exclude_replays": "true"}, headers=ALICE)).json()
    assert live["issues"][0]["runs"] == 7, "live traffic only"
    r = await api.client.get("/v1/traces/issues/runs", params={"key": listed[0]["key"]}, headers=ALICE)
    assert r.status_code == 200, r.text
    issue = r.json()["issue"]
    assert issue["runs"] == 7
    assert [e["run_id"] for e in issue["examples"]] == [f"broke-{i}" for i in reversed(range(7))]
    page = await api.client.get("/v1/traces/issues/runs", params={"key": listed[0]["key"], "limit": 2}, headers=ALICE)
    assert [e["run_id"] for e in page.json()["issue"]["examples"]] == ["broke-6", "broke-5"]
    assert (await api.client.get("/v1/traces/issues/runs", params={"key": "x:check:none:"},
                                 headers=ALICE)).status_code == 404
    assert (await api.client.get("/v1/traces/issues/runs", params={"key": listed[0]["key"]},
                                 headers=BOB)).status_code == 404


async def test_judges_are_set_up_over_the_api(api: Api) -> None:
    body = {"composition_id": "githi-v1", "name": "answers", "rubric": "Answers the question asked.", "sample_rate": 0.5}
    r = await api.client.put("/v1/traces/judges", json=body, headers=ALICE)
    assert r.status_code == 200, r.text
    assert r.json()["version"] == 1 and r.json()["enabled"] is True
    listed = (await api.client.get("/v1/traces/judges", params={"composition_id": "githi-v1"}, headers=ALICE)).json()
    assert [j["name"] for j in listed["judges"]] == ["answers"]
    bad = await api.client.put("/v1/traces/judges", json=body | {"rubric": "short"}, headers=ALICE)
    assert bad.status_code == 422
    assert (await api.client.delete(f"/v1/traces/judges/{r.json()['id']}", headers=ALICE)).status_code == 204
    assert (await api.client.delete(f"/v1/traces/judges/{r.json()['id']}", headers=ALICE)).status_code == 404


async def test_a_replay_for_an_experiment_is_judged_by_every_judge(api: Api) -> None:
    """A judge on a sample judges live runs on its share, but every replay an experiment makes:
    otherwise the two versions it compares are judged by different judges."""
    row, sid = await _finished_run(api)
    reader = JournalReader(api.factory)
    await save_judge(api.factory, tenant_id="t1", composition_id="githi-v1", name="rare",
                     rubric="Answers the question that was asked.", sample_rate=0.0001, model=None,
                     enabled=True, created_by="alice")
    verdict = FakeModelGatewayClient(assistant_text='{"verdict": "pass", "reason": "answers it"}')
    trace = await after_run(api.factory, reader, sid, "r1", lambda s: verdict)
    assert trace is not None
    assert await judge_run(api.factory, row, trace, verdict) == [], "live traffic: on its sample"

    marked = await api.client.put(f"/v1/sessions/{sid}/evaluation", json={"experiment_id": "exp-1"}, headers=ALICE)
    assert marked.status_code == 204, marked.text
    assert [v.label for v in await judge_run(api.factory, row, trace, verdict)] == ["pass"]
    again = await api.client.put(f"/v1/sessions/{sid}/evaluation", json={"experiment_id": "exp-2"}, headers=ALICE)
    assert again.status_code == 204
    assert (await api.client.put(f"/v1/sessions/{sid}/evaluation", json={"experiment_id": "x"}, headers=BOB)).status_code in (403, 404)


async def test_a_replay_with_a_good_answer_is_graded_against_it(api: Api) -> None:
    """Braintrust autoevals' ClosedQA: the task, the run's answer and the saved good answer as
    the criterion; a pass or fail recorded as the built-in ``good answer`` judge."""
    from supervisor.traces.judges import REFERENCE_JUDGE

    row, sid = await _finished_run(api)
    reader = JournalReader(api.factory)
    asked: list[list[object]] = []

    class Grader(FakeModelGatewayClient):
        def complete(self, messages, tools, **kw):  # type: ignore[no-untyped-def, override]
            asked.append(list(messages))
            return super().complete(messages, tools, **kw)

    grader = Grader(assistant_text='{"verdict": "fail", "reason": "it never ran the tests"}')
    trace = await after_run(api.factory, reader, sid, "r1", lambda s: grader)
    assert trace is not None and await judge_run(api.factory, row, trace, grader) == [], "no judges, no good answer"

    marked = await api.client.put(
        f"/v1/sessions/{sid}/evaluation",
        json={"experiment_id": "exp-1", "expected": "Runs the tests after the change and says they pass."},
        headers=ALICE,
    )
    assert marked.status_code == 204, marked.text
    given = await judge_run(api.factory, row, trace, grader)
    assert [(g.label, g.reason) for g in given] == [("fail", "it never ran the tests")]
    prompt = asked[-1][1].content  # type: ignore[attr-defined]
    assert "[Task]: fix it" in prompt and "[Criterion]: Runs the tests after the change and says they pass." in prompt
    async with api.factory() as db:
        scores = (await db.execute(select(TraceScoreRow).where(TraceScoreRow.source == "judge"))).scalars().all()
    assert [(s.name, s.label, s.evaluator) for s in scores] == [(REFERENCE_JUDGE, "fail", "reference:closed_qa@v1")]


def test_kappa_is_agreement_beyond_chance() -> None:
    """Cohen's kappa as sklearn.metrics.cohen_kappa_score: p_o = 0.7, p_e = 0.5 gives 0.4."""
    pairs = [(True, True)] * 20 + [(True, False)] * 5 + [(False, True)] * 10 + [(False, False)] * 15
    assert kappa(pairs) == 0.4
    assert kappa([(True, True), (False, False)]) == 1.0
    assert kappa([]) is None and kappa([(True, True)] * 3) is None, "undefined: chance explains it all"


async def test_a_judge_is_measured_against_what_people_said(api: Api, monkeypatch: pytest.MonkeyPatch) -> None:
    sid = await api.session()
    reader = JournalReader(api.factory)
    await save_judge(api.factory, tenant_id="t1", composition_id="githi-v1", name="answers",
                     rubric="Answers the question that was asked.", sample_rate=1.0, model=None,
                     enabled=True, created_by="alice")
    monkeypatch.setenv(JUDGE_MODEL_ENV, "judge-model")
    seen: list[str | None] = []

    class Says(FakeModelGatewayClient):
        def complete(self, messages, tools, *, model=None, tool_choice=None):  # type: ignore[no-untyped-def]
            seen.append(model)
            return super().complete(messages, tools, model=model, tool_choice=tool_choice)

    # the judge passes all four; people liked r0 and r1 and not r2 and r3
    for n in range(4):
        await api.run(sid, f"r{n}", f"ask {n}", start=1_000 * (n + 1))
        await after_run(api.factory, reader, sid, f"r{n}",
                        lambda s: Says(assistant_text='{"verdict": "pass", "reason": "fine"}'))
        await api.client.put(f"/v1/sessions/{sid}/runs/r{n}/feedback", json={"good": n < 2}, headers=ALICE)
    assert seen and set(seen) == {"judge-model"}, "a judge with no model of its own uses TRACE_JUDGE_MODEL"

    r = await api.client.get("/v1/traces/judges/agreement", params={"composition_id": "githi-v1"}, headers=ALICE)
    assert r.status_code == 200, r.text
    (judge,) = r.json()["judges"]
    assert (judge["name"], judge["runs"], judge["agreement"]) == ("answers", 4, 0.5)
    assert (judge["passed_but_marked_bad"], judge["failed_but_marked_good"]) == (2, 0)
    assert judge["kappa"] == 0.0, "it passes everything: no better than chance"
    bob = await api.client.get("/v1/traces/judges/agreement", params={"composition_id": "githi-v1"}, headers=BOB)
    assert bob.json()["judges"] == []


async def test_a_rubric_is_tried_on_recent_runs_before_it_is_saved(api: Api, monkeypatch: pytest.MonkeyPatch) -> None:
    body = {"composition_id": "githi-v1", "rubric": "Says what it changed and why."}
    r = await api.client.post("/v1/traces/judges/preview", json=body, headers=ALICE)
    assert r.status_code == 409 and r.json()["detail"]["error_code"] == "NO_RUNS"
    sid, replay = await api.session(), await api.session()
    for n in range(3):
        await api.run(sid, f"r{n}", f"ask {n}", start=1_000 * (n + 1))
    await api.run(replay, "x", "a replay", start=9_000)
    for session, run in ((sid, "r0"), (sid, "r1"), (sid, "r2"), (replay, "x")):  # as each run's end records it
        await record_run(api.factory, JournalReader(api.factory), session, run)
    await api.client.put(f"/v1/sessions/{replay}/evaluation", json={"experiment_id": "e"}, headers=ALICE)
    await api.client.put(f"/v1/sessions/{sid}/runs/r2/feedback", json={"good": False}, headers=ALICE)
    monkeypatch.setattr(type(api.state.manager), "session_model_client",
                        lambda self, s, row: FakeModelGatewayClient(assistant_text='{"verdict": "fail", "reason": "no why"}'))
    r = await api.client.post("/v1/traces/judges/preview", json=body | {"runs": 2}, headers=ALICE)
    assert r.status_code == 200, r.text
    runs = r.json()["runs"]
    assert [(x["run_id"], x["verdict"], x["people"]) for x in runs] == [("r2", "fail", "bad"), ("r1", "fail", None)]
    async with api.factory() as db:  # a preview keeps nothing
        assert (await db.execute(select(TraceScoreRow).where(TraceScoreRow.source == "judge"))).first() is None


async def test_runs_a_person_should_mark_wait_in_a_review_queue(api: Api) -> None:
    """Where automatic verdicts are least to be trusted, until a person marks the run
    (Langfuse's annotation queue: an item is pending until it is scored)."""
    sid, replay = await api.session(), await api.session()
    reader = JournalReader(api.factory)
    await save_judge(api.factory, tenant_id="t1", composition_id="githi-v1", name="answers",
                     rubric="Answers the question that was asked.", sample_rate=1.0, model=None,
                     enabled=True, created_by="alice")
    replies = {"unsure": "no idea", "fine-but-broke": '{"verdict": "pass", "reason": "fine"}',
               "fine": '{"verdict": "pass", "reason": "fine"}', "marked": "no idea"}
    for n, (run, reply) in enumerate(replies.items()):
        await api.run(sid, run, f"ask {run}", start=1_000 * (n + 1), tool_ok=run != "fine-but-broke")
        await after_run(api.factory, reader, sid, run, lambda s, r=reply: FakeModelGatewayClient(assistant_text=r))
    await api.run(replay, "x", "a replay", start=9_000)
    await api.client.put(f"/v1/sessions/{replay}/evaluation", json={"experiment_id": "e"}, headers=ALICE)
    await after_run(api.factory, reader, replay, "x", lambda s: FakeModelGatewayClient(assistant_text="no idea"))
    await api.client.put(f"/v1/sessions/{sid}/runs/marked/feedback", json={"good": True}, headers=ALICE)

    r = await api.client.get("/v1/traces/review", params={"composition_id": "githi-v1"}, headers=ALICE)
    assert r.status_code == 200, r.text
    queue = r.json()["runs"]
    assert [(q["run_id"], q["why"]) for q in queue] == [("fine-but-broke", "checks_disagree"), ("unsure", "unclear")]
    assert queue[0]["says"] == "a judge passed what a check failed" and queue[0]["checks_failed"] == ["tool_errors"]

    await api.client.put(f"/v1/sessions/{sid}/runs/unsure/feedback", json={"good": False}, headers=ALICE)
    after = (await api.client.get("/v1/traces/review", params={"composition_id": "githi-v1"}, headers=ALICE)).json()
    assert [q["run_id"] for q in after["runs"]] == ["fine-but-broke"], "marked: off the queue"
    assert (await api.client.get("/v1/traces/review", params={"composition_id": "githi-v1"},
                                 headers=BOB)).json()["runs"] == []
