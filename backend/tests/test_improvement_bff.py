"""Gated self-improvement: a change is learned from the traces, published as a candidate that is
not live, replayed on saved runs beside the live version, refused if it does worse, and made the
next version only when a person approves it."""

from __future__ import annotations

import json
import re
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import select

from prime_agent import config
from prime_agent.config import Settings, get_settings
from prime_agent.db.models.evaluation import Dataset, DatasetItem
from prime_agent.db.models.harness_release import HarnessDraft
from prime_agent.db.models.session_link import SessionLink
from prime_agent.main import create_app
from prime_agent.schemas.ai_identity import TenantPrincipal
from prime_agent.services.ai_client import AiClient
from prime_agent.services.evaluation_service import DEFAULT_TRIALS, EvaluationService
from prime_agent.services.harness_publish_service import HarnessPublisher, artifact_prefix, releases
from prime_agent.services.harness_service import HarnessService
from prime_agent.services.improvement_service import ImprovementService
from prime_agent.web.dependencies import get_current_user, get_db

REGISTRY = [
    {"name": n, "description": n, "tool_class": c, "parameters": {"type": "object", "properties": {}}}
    for n, c in (("read", "pure"), ("grep", "pure"), ("ls", "pure"), ("write", "idempotent"), ("edit", "idempotent"),
                 ("bash", "non_atomic"))
]
BASE = {"harness_version": "githi-v1", "runtime": "githi", "manifest": {"runtime": "githi", "image_digest": "img@sha256:abc"}}
FIXED = "Before answering, run the tests and read their output."


@pytest.fixture
def settings(monkeypatch):
    s = Settings(
        environment="development",
        secret_key="user-secret-key-also-at-least-32-bytes!!",
        ai_supervisor_base_url="http://supervisor.test",
        ai_service_jwt_secret="ai-service-jwt-secret-at-least-32-bytes!!",
    )
    get_settings.cache_clear()
    for target in ("prime_agent.config.get_settings", "prime_agent.config.settings.get_settings",
                   "prime_agent.services.ai_client.get_settings", "prime_agent.security.service_jwt.get_settings",
                   "prime_agent.web.routes.evaluations.get_settings"):
        monkeypatch.setattr(target, lambda: s)
    yield s
    get_settings.cache_clear()


class FakeSupervisor:
    """The catalog, sessions, runs, traces and reflection, as the supervisor answers them.

    ``live`` and ``candidate``: how a replay on each goes ("ok" or "fail").
    """

    def __init__(self, candidate: str = "ok", reflection: dict | None = None, live: str = "fail") -> None:
        self.candidate, self.live = candidate, live
        self.marked: list[str] = []
        self.reflection = reflection if reflection is not None else {
            "proposal": {"persona": "ignored", "rationale": "Runs answer before testing; say to test first.",
                         "changed": ["note-00001"],
                         "sections": [{"id": "intro", "text": "a rewrite the loop must ignore"}],
                         "learned": [{"id": "note-00001", "text": FIXED}],
                         "edits": [{"action": "add", "id": "note-00001", "text": FIXED,
                                    "reason": "tests fail after changes"}],
                         "skipped": ["edit 2: unknown action 'delete'"],
                         "expected_outcome": "runs test before answering"},
            "evidence": [{"key": "k", "title": "Tests failing after a change", "runs": 3,
                          "examples": [{"session_id": "ai-src", "run_id": "r0", "input": "fix the parser",
                                        "summary": "Changed parser.py; answered.", "reason": "tests failed"}]}],
        }
        self.published: list[tuple[str, dict]] = []
        self.sessions: dict[str, str] = {}  # ai session id -> harness version
        self.reflected: list[dict] = []
        self.by_round: dict[int, str] = {}  # a search's round -> how its candidate's replays go
        self.later: list[dict] = []  # replies to the reflections after the first, in order

    def _good(self, sid: str) -> bool:
        found = re.search(r"-c[0-9a-f]{8}(?:r(\d))?$", self.sessions.get(sid, ""))  # h-<id>-c<tag>[r<n>], not h-c…-r1
        if found is None:
            return self.live == "ok"
        return self.by_round.get(int(found.group(1) or 1), self.candidate) == "ok"

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path, method = request.url.path, request.method
        body = json.loads(request.content) if request.content else {}
        if path == "/v1/tools":
            return httpx.Response(200, json=REGISTRY)
        if path == "/v1/harness_versions/githi-v1":
            return httpx.Response(200, json=BASE)
        if path == "/v1/harness_versions":
            self.published.append(("harness_version", body))
            return httpx.Response(201, json={"harness_version": body["harness_version"], "runtime": "githi"})
        if path.startswith("/v1/catalog/") and path.endswith("/publish"):
            kind = path.split("/")[3]
            self.published.append((kind, body))
            return httpx.Response(200, json={"artifact_id": body["name"], "digest": f"sha256:{kind}{len(self.published)}"})
        if path == "/v1/traces/reflect":
            self.reflected.append(body)
            reply = self.later[len(self.reflected) - 2] if len(self.reflected) > 1 and self.later else self.reflection
            return httpx.Response(200, json=reply)
        if path == "/v1/sessions" and method == "POST":
            sid = uuid4().hex
            self.sessions[sid] = body["harness_version"]
            return httpx.Response(202, json={"session_id": sid, "status": "starting"})
        sid = path.split("/")[3] if path.startswith("/v1/sessions/") else ""
        if path.endswith("/evaluation") and method == "PUT":  # a replay: judged by every judge
            self.marked.append(sid)
            return httpx.Response(204)
        if path.endswith("/startup"):
            return httpx.Response(200, json={"status": "ready"})
        if path.endswith("/messages"):
            return httpx.Response(202, json={"turn_id": f"run-{sid[:6]}"})
        if "/runs/" in path and path.endswith("/trace"):
            good = self._good(sid)
            return httpx.Response(200, json={
                "session_id": sid, "run_id": path.split("/")[5], "trace_id": "t" * 32,
                "summary": "Ran the tests; answered." if good else "Changed a file; answered.",
                "rollup": {"status": "ok", "cost": 0.02, "duration_ms": 900},
                "spans": [{"id": "root", "parent_id": None, "kind": "agent", "name": "x", "attrs": {}}],
                "scores": [{"id": "c", "span_id": None, "name": "tests_after_change", "topic": None, "source": "check",
                            "label": "pass" if good else "fail", "value": None, "reason": None, "comment": None,
                            "evaluator": "checks@2", "principal_id": None, "created_ms": 1}],
            })
        if "/runs/" in path:
            return httpx.Response(200, json={"status": "completed"})
        if method == "DELETE":
            return httpx.Response(204)
        return httpx.Response(404, json={"detail": f"unexpected {method} {path}"})


@pytest.fixture
async def owner(make_user):
    return await make_user(email="improve@example.com", name="Improve")


async def _published_agent(db, owner, fake: FakeSupervisor):
    agent = await HarnessService.acreate(
        {"name": "fixer", "model": "gateway/echo", "mode": "sticky", "user_id": owner.id, "created_by": owner.id}, db
    )
    client = AiClient(config.get_settings(), transport=httpx.MockTransport(fake))
    await HarnessPublisher(client).publish(agent, user_id=owner.id, identity=TenantPrincipal.from_user(owner), db=db)
    ds = Dataset(user_id=owner.id, harness_id=agent.id, name="regressions")
    db.add(ds)
    await db.flush()
    db.add(DatasetItem(dataset_id=ds.id, input="fix the parser", source_status="ok"))
    db.add(DatasetItem(dataset_id=ds.id, input="add a flag", source_status="ok"))
    await db.commit()
    return agent, ds


def _api(db, user, fake: FakeSupervisor, monkeypatch, started: list) -> httpx.AsyncClient:
    def improver(s=None) -> ImprovementService:
        return ImprovementService(config.get_settings(), client=AiClient(config.get_settings(), transport=httpx.MockTransport(fake)))

    monkeypatch.setattr("prime_agent.web.routes.evaluations.ImprovementService.from_settings", improver)

    monkeypatch.setattr("prime_agent.web.routes.evaluations.start_improving",
                        lambda service, proposal_id, identity: started.append(proposal_id))
    app = create_app()

    async def override_db():
        yield db

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = lambda: user
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


def _same(db):
    @asynccontextmanager
    async def factory():
        yield db

    return factory


async def _propose(api, db, agent, ds, started, **body):
    r = await api.post(f"/api/v1/traces/agents/{agent.id}/proposals", json={"dataset_id": ds.id, **body})
    assert r.status_code == 202, r.text
    assert started.pop() == r.json()["id"]  # started in the background; run here with no polling delay
    service = ImprovementService(config.get_settings(), client=AiClient(config.get_settings(), transport=httpx.MockTransport(api.fake)))
    await service.run(r.json()["id"], identity=TenantPrincipal.from_user(api.user), session_factory=_same(db), poll_s=0)
    return (await api.get(f"/api/v1/traces/proposals/{r.json()['id']}")).json()


@pytest.mark.asyncio
async def test_a_change_that_does_better_waits_for_approval_and_becomes_the_next_version(db_session, owner, settings, monkeypatch):
    fake = FakeSupervisor(candidate="ok")
    agent, ds = await _published_agent(db_session, owner, fake)
    live_digest = agent.composition_digest
    started: list = []
    async with _api(db_session, owner, fake, monkeypatch, started) as api:
        api.fake, api.user = fake, owner
        p = await _propose(api, db_session, agent, ds, started, notes="it skips the tests")
        assert p["status"] == "awaiting_approval", p
        assert p["gate"]["passed"] and p["gate"]["better"] == 2 and p["gate"]["worse"] == 0
        assert p["changed"] == ["note-00001"] and p["rationale"].startswith("Runs answer before testing")
        assert [(e["action"], e["id"], e["reason"]) for e in p["edits"]] == [
            ("add", "note-00001", "tests fail after changes")]
        assert p["skipped_edits"] == ["edit 2: unknown action 'delete'"]
        assert p["expected_outcome"] == "runs test before answering"
        assert p["candidate_prompt"]["learned"] == [{"id": "note-00001", "text": FIXED}]
        assert p["candidate_prompt"]["sections"] == p["base_prompt"]["sections"], "the person's prompt is never changed"
        assert p["candidate_prompt"]["persona"] == p["base_prompt"]["persona"] and p["base_prompt"]["learned"] == []
        assert p["evidence"][0]["title"] == "Tests failing after a change"
        assert p["evidence"][0]["examples"][0]["session_id"] is None  # not a session of the user's: not linked
        assert (p["baseline"]["label"], p["candidate"]["label"]) == ("v1", f"candidate {p['id'][:6]}")

        # the reflection read the live prompt and the person's note, never the replays
        (asked,) = fake.reflected
        assert asked["notes"] == "it skips the tests" and asked["exclude_sessions"] == []
        assert asked["history"] == [], "the first change: nothing was proposed before"
        # the candidate is published under its own name, and is not live
        hv = [b["harness_version"] for k, b in fake.published if k == "harness_version"]
        assert hv == [f"{artifact_prefix(agent.id)}-r1", f"{artifact_prefix(agent.id)}-c{p['id'].replace('-', '')[:8]}"]
        await db_session.refresh(agent)
        assert agent.composition_digest == live_digest and len(await releases(agent.id, db_session)) == 1

        inbox = (await api.get("/api/v1/traces/proposals", params={"status_filter": "awaiting_approval"})).json()
        assert [x["id"] for x in inbox] == [p["id"]]

        published = len(fake.published)
        approved = await api.post(f"/api/v1/traces/proposals/{p['id']}/approve")
        assert approved.status_code == 200, approved.text
        assert approved.json()["status"] == "approved" and approved.json()["release_version"] == 2
        assert len(fake.published) == published  # the measured candidate itself: nothing rebuilt
        await db_session.refresh(agent)
        v2 = (await releases(agent.id, db_session))[0]
        assert v2.version == 2 and agent.composition_digest == v2.composition_digest != live_digest
        assert agent.harness_version.endswith(f"-c{p['id'].replace('-', '')[:8]}")
        assert v2.manifest["prompt"]["learned"] == [{"id": "note-00001", "text": FIXED}]
        draft = await db_session.get(HarnessDraft, agent.id)
        assert draft is not None and draft.manifest["prompt"]["learned"][0]["text"] == FIXED
        # the notes reach the agent as one section after the person's
        (pack,) = [b for k, b in fake.published if k == "prompt_pack"][-1:]
        assert pack["sections"][-1]["id"] == "learned" and FIXED in pack["sections"][-1]["text"]

        again = await api.post(f"/api/v1/traces/proposals/{p['id']}/approve")
        assert again.status_code == 409

        # the next reflection never sees the sessions the first change was measured in
        await _propose(api, db_session, agent, ds, started)
        # 2 saved runs, each replayed DEFAULT_TRIALS times, on the live version and the candidate
        assert len(fake.reflected[1]["exclude_sessions"]) == 2 * DEFAULT_TRIALS * 2
        # and it reads what became of the first: approved, with what it changed
        (past,) = fake.reflected[1]["history"]
        assert past["outcome"] == "approved" and past["why"][0] == "a person approved it: it became v2"
        assert past["edits"] == [{"action": "add", "id": "note-00001", "text": FIXED}]


@pytest.mark.asyncio
@pytest.mark.parametrize(("live", "candidate", "why"), [
    ("ok", "fail", "it did worse on 2 of 2 saved runs"),
    ("fail", "fail", "it did no better on any of 2 saved runs"),
])
async def test_a_change_that_does_not_do_better_is_refused_and_cannot_be_approved(
    db_session, owner, settings, monkeypatch, live, candidate, why
):
    fake = FakeSupervisor(candidate=candidate, live=live)
    agent, ds = await _published_agent(db_session, owner, fake)
    started: list = []
    async with _api(db_session, owner, fake, monkeypatch, started) as api:
        api.fake, api.user = fake, owner
        p = await _propose(api, db_session, agent, ds, started)
        assert p["status"] == "refused"
        assert not p["gate"]["passed"] and why in p["gate"]["reasons"]
        refused = await api.post(f"/api/v1/traces/proposals/{p['id']}/approve")
        assert refused.status_code == 409 and refused.json()["detail"]["error_code"] == "NOT_AWAITING_APPROVAL"
        inbox = (await api.get("/api/v1/traces/proposals", params={"status_filter": "awaiting_approval"})).json()
        assert inbox == []  # a refused change never reaches anyone
        assert (await api.post(f"/api/v1/traces/proposals/{p['id']}/reject")).json()["status"] == "rejected"
        # the next reflection reads why: so it does not propose the same change again
        await _propose(api, db_session, agent, ds, started)
        (past,) = fake.reflected[1]["history"]
        assert past["outcome"] == "rejected" and f"the gate refused it: {why}" in past["why"]
        assert not any("passed the gate" in w for w in past["why"])
    assert len(await releases(agent.id, db_session)) == 1


@pytest.mark.asyncio
async def test_nothing_to_change_publishes_nothing(db_session, owner, settings, monkeypatch):
    fake = FakeSupervisor(reflection={"proposal": {"rationale": "the failures are the sandbox's, not the prompt's",
                                                   "changed": [], "sections": []}, "evidence": []})
    agent, ds = await _published_agent(db_session, owner, fake)
    started: list = []
    async with _api(db_session, owner, fake, monkeypatch, started) as api:
        api.fake, api.user = fake, owner
        before = len(fake.published)
        p = await _propose(api, db_session, agent, ds, started)
    assert p["status"] == "no_change" and "sandbox" in p["rationale"]
    assert len(fake.published) == before and not fake.sessions


@pytest.mark.asyncio
async def test_a_change_measured_against_an_older_version_cannot_be_approved(db_session, owner, settings, monkeypatch):
    fake = FakeSupervisor(candidate="ok")
    agent, ds = await _published_agent(db_session, owner, fake)
    started: list = []
    async with _api(db_session, owner, fake, monkeypatch, started) as api:
        api.fake, api.user = fake, owner
        p = await _propose(api, db_session, agent, ds, started)
        assert p["status"] == "awaiting_approval"
        # the person publishes something else meanwhile
        draft = await db_session.get(HarnessDraft, agent.id)
        manifest = dict(draft.manifest) if draft else dict((await releases(agent.id, db_session))[0].manifest)
        manifest["policy"] = {**manifest.get("policy", {}), "max_steps": 7}
        db_session.add(HarnessDraft(harness_id=agent.id, manifest=manifest)) if draft is None else setattr(draft, "manifest", manifest)
        await db_session.commit()
        client = AiClient(config.get_settings(), transport=httpx.MockTransport(fake))
        await HarnessPublisher(client).publish(agent, user_id=owner.id, identity=TenantPrincipal.from_user(owner), db=db_session)
        stale = await api.post(f"/api/v1/traces/proposals/{p['id']}/approve")
    assert stale.status_code == 409 and stale.json()["detail"]["error_code"] == "STALE_PROPOSAL"


@pytest.mark.asyncio
async def test_one_change_at_a_time_and_only_on_your_own_agent(db_session, owner, settings, monkeypatch, make_user):
    fake = FakeSupervisor()
    agent, ds = await _published_agent(db_session, owner, fake)
    started: list = []
    async with _api(db_session, owner, fake, monkeypatch, started) as api:
        first = await api.post(f"/api/v1/traces/agents/{agent.id}/proposals", json={"dataset_id": ds.id})
        busy = await api.post(f"/api/v1/traces/agents/{agent.id}/proposals", json={"dataset_id": ds.id})
        assert first.status_code == 202 and busy.json()["detail"]["error_code"] == "PROPOSAL_RUNNING"
    stranger = await make_user(email="stranger-improve@example.com", name="S")
    async with _api(db_session, stranger, fake, monkeypatch, []) as api:
        assert (await api.post(f"/api/v1/traces/agents/{agent.id}/proposals", json={"dataset_id": ds.id})).status_code == 404
        assert (await api.get(f"/api/v1/traces/proposals/{first.json()['id']}")).status_code == 404
        assert (await api.post(f"/api/v1/traces/proposals/{first.json()['id']}/approve")).status_code == 404
        assert (await api.get("/api/v1/traces/proposals")).json() == []


def test_a_run_judged_on_one_version_only_is_not_compared():
    from datetime import UTC, datetime

    from prime_agent.schemas.evaluation import (
        ComparisonRow,
        ExperimentComparison,
        ExperimentItemResult,
        ExperimentOut,
        ExperimentStats,
        Verdict,
    )
    from prime_agent.services.improvement_service import decide_gate

    stats = ExperimentStats(items=1, finished=1, answered=1, failed=0, needs_approval=0, checks_failed=0,
                            judge_pass=0, judge_fail=0)
    exp = ExperimentOut(id="e", dataset_id="d", label="x", status="done", created_at=datetime.now(UTC), stats=stats)

    def result(verdicts):
        return ExperimentItemResult(item_id="i", position=0, input="q", status="done", run_status="ok", verdicts=verdicts)

    row = ComparisonRow(item_id="i", input="q", change="better",
                        a=result([Verdict(name="shouts", source="judge", label="fail")]), b=result([]))
    gate = decide_gate(ExperimentComparison(a=exp, b=exp, rows=[row], better=1, worse=0))
    assert not gate.passed and gate.reasons == ["1 saved run was not judged on both versions"]


def test_a_candidate_that_passes_every_trial_on_fewer_runs_is_refused() -> None:
    """pass^k (Anthropic's guide to agent evals): a change may not trade consistency away."""
    from datetime import UTC, datetime

    from prime_agent.schemas.evaluation import ComparisonRow, ExperimentComparison, ExperimentOut, ExperimentStats
    from prime_agent.services.improvement_service import decide_gate

    def exp(label: str, pass_all: int) -> ExperimentOut:
        stats = ExperimentStats(items=2, finished=2, answered=2, failed=0, needs_approval=0, checks_failed=0,
                                judge_pass=0, judge_fail=0, trials=3, passed=4, pass_all=pass_all, pass_any=2)
        return ExperimentOut(id=label, dataset_id="d", label=label, status="done", created_at=datetime.now(UTC), stats=stats)

    rows = [ComparisonRow(item_id="i1", input="x", change="better"), ComparisonRow(item_id="i2", input="y", change="same")]
    gate = decide_gate(ExperimentComparison(a=exp("v1", 2), b=exp("cand", 1), rows=rows, better=1, worse=0))
    assert not gate.passed and "it passed every trial on 1 saved runs, against 2" in gate.reasons
    assert decide_gate(ExperimentComparison(a=exp("v1", 1), b=exp("cand", 1), rows=rows, better=1, worse=0)).passed


def test_a_candidate_that_costs_or_takes_much_more_per_run_is_refused() -> None:
    """Cost and time are part of a run's quality: the owner's limits, not a free trade."""
    from datetime import UTC, datetime

    from prime_agent.schemas.evaluation import ComparisonRow, ExperimentComparison, ExperimentOut, ExperimentStats
    from prime_agent.services.improvement_service import decide_gate

    def exp(label: str, cost: float, ms: int) -> ExperimentOut:
        stats = ExperimentStats(items=1, finished=1, answered=1, failed=0, needs_approval=0, checks_failed=0,
                                judge_pass=0, judge_fail=0, avg_cost=cost, avg_duration_ms=ms)
        return ExperimentOut(id=label, dataset_id="d", label=label, status="done", created_at=datetime.now(UTC), stats=stats)

    rows = [ComparisonRow(item_id="i1", input="x", change="better")]

    def gate(cost: float, ms: int, **limits: float):  # type: ignore[no-untyped-def]
        return decide_gate(ExperimentComparison(a=exp("v1", 0.10, 1000), b=exp("c", cost, ms), rows=rows, better=1, worse=0),
                           **limits)

    assert gate(0.12, 1500).passed, "within 20% more cost and 50% more time"
    costly = gate(0.25, 1000)
    assert not costly.passed and "a run costs 2.5× the live version's (at most 1.2× allowed)" in costly.reasons
    slow = gate(0.10, 3000)
    assert not slow.passed and "a run takes 3.0× as long as the live version's (at most 1.5× allowed)" in slow.reasons
    assert gate(0.25, 3000, max_cost_increase=2.0, max_time_increase=2.0).passed, "the owner may allow more"


def test_a_candidate_better_only_on_runs_it_learnt_from_is_refused() -> None:
    """GEPA keeps candidates on a held-out validation set, not on the minibatches it reflected on."""
    from datetime import UTC, datetime

    from prime_agent.schemas.evaluation import ComparisonRow, ExperimentComparison, ExperimentOut, ExperimentStats
    from prime_agent.services.improvement_service import decide_gate

    stats = ExperimentStats(items=3, finished=3, answered=3, failed=0, needs_approval=0, checks_failed=0,
                            judge_pass=0, judge_fail=0)
    exp = ExperimentOut(id="e", dataset_id="d", label="x", status="done", created_at=datetime.now(UTC), stats=stats)

    def gate(held_change: str):  # type: ignore[no-untyped-def]
        rows = [ComparisonRow(item_id="i1", input="a", change="better"), ComparisonRow(item_id="i2", input="b", change="same"),
                ComparisonRow(item_id="i3", input="c", change=held_change, held_out=True)]
        better = sum(1 for r in rows if r.change == "better")
        return decide_gate(ExperimentComparison(a=exp, b=exp, rows=rows, better=better, worse=0))

    fitted = gate("same")
    assert not fitted.passed
    assert fitted.reasons == ["it did no better on any of the 1 held-out saved runs, only on runs it learnt from"]
    general = gate("better")
    assert general.passed and (general.held_out_better, general.held_out_worse) == (1, 0)
    assert general.reasons == ["better on 2 and worse on none of 3 saved runs, 1 of the 1 held out"]
    assert [i.held_out for i in general.items] == [False, False, True]


@pytest.mark.asyncio
async def test_the_optimizer_never_reads_a_held_out_runs_conversation(db_session, owner, settings, monkeypatch):
    fake = FakeSupervisor(candidate="ok")
    agent, ds = await _published_agent(db_session, owner, fake)
    for ai_id, held in (("ai-learn", False), ("ai-held", True)):
        link = SessionLink(id=str(uuid4()), user_id=owner.id, harness_id=agent.id, ai_session_id=ai_id, tenant_id=owner.id,
                           harness_version="x", runtime="githi", composition_id=agent.composition_id,
                           composition_digest=agent.composition_digest, status="active",
                           created_at=datetime.now(UTC), updated_at=datetime.now(UTC))
        db_session.add(link)
        await db_session.flush()
        db_session.add(DatasetItem(dataset_id=ds.id, input=f"from {ai_id}", source_session_id=link.id, source_run_id="r1",
                                   held_out=held))
    await db_session.commit()
    started: list = []
    async with _api(db_session, owner, fake, monkeypatch, started) as api:
        api.fake, api.user = fake, owner
        await _propose(api, db_session, agent, ds, started)
    assert fake.reflected[0]["exclude_sessions"] == ["ai-held"]


def test_the_next_candidate_edits_a_version_on_the_pareto_front() -> None:
    """GEPA's select_program_candidate_from_pareto_front: a version best on some saved run can
    be picked; one that is best only where another version also is, and scores less, is not."""
    import random

    from prime_agent.services.improvement_service import pareto_parent

    live = {"a": 5.0, "b": 5.0, "c": 5.0}
    first = {"a": 10.0, "b": 5.0, "c": 5.0}  # best on a
    second = {"a": 5.0, "b": 10.0, "c": 10.0}  # best on b and c
    rng = random.Random(1)
    drawn = {pareto_parent([live, first, second], set(), rng) for _ in range(200)}
    assert drawn == {1, 2}, "live is on no front of its own"
    # with held-out runs, only they decide the fronts (GEPA's valset)
    assert {pareto_parent([live, first, second], {"a"}, rng) for _ in range(50)} == {1}
    # a version tied on every front with a better one is dropped (remove_dominated_programs)
    tied = {"a": 10.0, "b": 0.0, "c": 0.0}
    assert {pareto_parent([live, first, second, tied], set(), rng) for _ in range(200)} == {1, 2}
    assert pareto_parent([{"a": None}], set(), rng) == 0, "nothing finished: the live version"


@pytest.mark.asyncio
async def test_a_search_keeps_trying_and_offers_the_best_candidate_it_kept(db_session, owner, settings, monkeypatch):
    fake = FakeSupervisor(candidate="ok")
    fake.by_round = {1: "fail", 2: "ok"}  # the first change does not help; the second does
    second = "Run the tests, and read every failure before answering."
    fake.later = [{
        "proposal": {"persona": "coder", "rationale": "Say to read failures.", "changed": ["note-00001"],
                     "learned": [{"id": "note-00001", "text": second}],
                     "edits": [{"action": "add", "id": "note-00001", "text": second, "reason": "r"}],
                     "skipped": [], "expected_outcome": "fewer failing runs"},
        "evidence": [],
    }]
    agent, ds = await _published_agent(db_session, owner, fake)
    started: list = []
    async with _api(db_session, owner, fake, monkeypatch, started) as api:
        api.fake, api.user = fake, owner
        p = await _propose(api, db_session, agent, ds, started, rounds=2)
        assert p["status"] == "awaiting_approval", p
        assert [(r["round"], r["parent"], r["kept"]) for r in p["rounds"]] == [(1, 0, False), (2, 0, True)]
        assert p["rounds"][0]["why"].startswith("scored ") and "on the 2 saved runs it may learn from" in p["rounds"][0]["why"]
        assert p["candidate_prompt"]["learned"] == [{"id": "note-00001", "text": second}]
        assert p["rationale"] == "Say to read failures." and p["candidate"]["label"] == f"candidate {p['id'][:6]} round 2"
        # the second reflection read what became of the first round
        assert fake.reflected[1]["history"][-1]["outcome"] == "dropped in this search"
        assert fake.reflected[1]["learned"] == [], "its parent is the live version: the first was dropped"
        approved = (await api.post(f"/api/v1/traces/proposals/{p['id']}/approve")).json()
        assert approved["status"] == "approved"
        await db_session.refresh(agent)
        assert agent.harness_version.endswith(f"-c{p['id'].replace('-', '')[:8]}r2"), "the measured round-2 candidate"


@pytest.mark.asyncio
async def test_a_search_offers_the_first_change_when_later_ones_do_not_pass(db_session, owner, settings, monkeypatch):
    fake = FakeSupervisor(candidate="ok")
    fake.by_round = {1: "ok", 2: "fail"}
    fake.later = [{"proposal": {"rationale": "nothing more", "changed": [], "sections": []}, "evidence": []}]
    agent, ds = await _published_agent(db_session, owner, fake)
    started: list = []
    async with _api(db_session, owner, fake, monkeypatch, started) as api:
        api.fake, api.user = fake, owner
        p = await _propose(api, db_session, agent, ds, started, rounds=3)
        assert p["status"] == "awaiting_approval" and len(p["rounds"]) == 1, "no more to change: the search stops"
        assert p["candidate_prompt"]["learned"][0]["text"] == FIXED and p["max_rounds"] == 3
        # round 2's reflection edited the kept first candidate (the only version on the front)
        assert fake.reflected[1]["learned"] == [{"id": "note-00001", "text": FIXED}]


@pytest.mark.asyncio
async def test_an_agent_proposes_a_change_by_itself_when_an_issue_keeps_growing(
    db_session, owner, settings, monkeypatch, make_user
):
    """Prime Agent's auto-refine gates, in order: on; not while one runs; a cooldown every
    attempt stamps; enough failing runs since the last attempt (live traffic only)."""
    from datetime import timedelta

    from prime_agent.db.models.evaluation import AutoImprove, Proposal
    from prime_agent.services.auto_improve_service import AutoImprover

    fake = FakeSupervisor(candidate="ok")
    agent, ds = await _published_agent(db_session, owner, fake)
    link = SessionLink(id=str(uuid4()), user_id=owner.id, harness_id=agent.id, ai_session_id="ai-live", tenant_id=owner.id,
                       harness_version="x", runtime="githi", composition_id=agent.composition_id,
                       composition_digest=agent.composition_digest, status="active",
                       created_at=datetime.now(UTC), updated_at=datetime.now(UTC))
    db_session.add(link)
    await db_session.commit()
    failing = {"runs": 2}
    asked: list[dict] = []
    plain = fake.__call__

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/traces/issues":
            asked.append(dict(request.url.params))
            return httpx.Response(200, json={"issues": [{
                "key": f"{agent.composition_id}:check:tests_after_change:", "composition_id": agent.composition_id,
                "title": "Tests failing after a change", "source": "check", "name": "tests_after_change", "topic": None,
                "runs": failing["runs"], "last_seen_ms": 1, "versions": [],
                "examples": [{"session_id": "ai-live", "run_id": "r1", "input": "fix it"}]}]})
        if request.url.path == "/v1/traces/issues/runs":
            return httpx.Response(200, json={"issue": {"examples": [{"session_id": "ai-live", "run_id": "r1"}]}})
        return plain(request)

    started: list[str] = []
    auto = AutoImprover(config.get_settings(), client=AiClient(config.get_settings(), transport=httpx.MockTransport(handler)))
    tick = lambda: auto.tick(_same(db_session), lambda service, pid, identity: started.append(pid))  # noqa: E731

    async with _api(db_session, owner, fake, monkeypatch, []) as api:
        assert (await api.get(f"/api/v1/traces/agents/{agent.id}/auto-improve")).json() is None
        r = await api.put(f"/api/v1/traces/agents/{agent.id}/auto-improve",
                          json={"dataset_id": ds.id, "min_runs": 3, "cooldown_minutes": 60})
        assert r.status_code == 200, r.text
        assert (r.json()["enabled"], r.json()["min_runs"], r.json()["last_attempt_at"]) == (True, 3, None)

        assert await tick() == [], "2 failing runs: below 3"
        row = await db_session.get(AutoImprove, agent.id)
        assert row.last_attempt_at is None, "not enough evidence stamps nothing"
        assert "failed 2 runs since the last attempt; a change is proposed at 3" in row.last_note
        assert asked[-1]["exclude_replays"] == "true" and "since_ms" in asked[-1]

        failing["runs"] = 4
        (pid,) = await tick()
        assert started == [pid]
        proposal = await db_session.get(Proposal, pid)
        assert proposal.notes.startswith("Started automatically: “Tests failing after a change” failed 4 runs")
        row = await db_session.get(AutoImprove, agent.id)
        assert row.last_attempt_at is not None and row.last_proposal_id == pid and "started a change" in row.last_note

        assert await tick() == [], "one round at a time"
        proposal.status = "refused"
        await db_session.commit()
        assert await tick() == [], "the cooldown: every attempt stamps it"
        row.last_attempt_at = datetime.now(UTC) - timedelta(minutes=61)
        await db_session.commit()
        assert len(await tick()) == 1, "after the cooldown, the next attempt"

        row.enabled = False
        await db_session.commit()
        assert await tick() == []

    stranger = await make_user(email="stranger-auto@example.com", name="S")
    async with _api(db_session, stranger, fake, monkeypatch, []) as api:
        assert (await api.get(f"/api/v1/traces/agents/{agent.id}/auto-improve")).status_code == 404
        put = await api.put(f"/api/v1/traces/agents/{agent.id}/auto-improve", json={"dataset_id": ds.id})
        assert put.status_code == 404


async def _approved(db_session, owner, monkeypatch, fake):
    agent, ds = await _published_agent(db_session, owner, fake)
    started: list = []
    async with _api(db_session, owner, fake, monkeypatch, started) as api:
        api.fake, api.user = fake, owner
        p = await _propose(api, db_session, agent, ds, started)
        approved = (await api.post(f"/api/v1/traces/proposals/{p['id']}/approve")).json()
    assert approved["watch"]["status"] == "watching" and approved["watch"]["previous_version"] == 1
    return agent, approved


def _watcher(fake, live: dict):  # type: ignore[no-untyped-def]
    """A watcher whose supervisor answers version quality from ``live`` (digest -> stats)."""
    from prime_agent.services.release_watch_service import ReleaseWatcher

    plain = fake.__call__

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/traces/versions":
            assert request.url.params["exclude_replays"] == "true", "live traffic only"
            return httpx.Response(200, json={"versions": [{"composition_digest": d, **v} for d, v in live.items()]})
        return plain(request)

    return ReleaseWatcher(config.get_settings(), client=AiClient(config.get_settings(), transport=httpx.MockTransport(handler)))


@pytest.mark.asyncio
async def test_an_approved_change_that_does_worse_live_is_rolled_back(db_session, owner, settings, monkeypatch):
    """Flagger's canary analysis: a check needs new traffic; failed checks add up; at the
    threshold the previous version is live again (as a new version, its exact artifacts)."""
    from prime_agent.db.models.evaluation import Proposal

    fake = FakeSupervisor(candidate="ok")
    agent, approved = await _approved(db_session, owner, monkeypatch, fake)
    v1, v2 = sorted(await releases(agent.id, db_session), key=lambda r: r.version)
    live = {v1.composition_digest: {"runs": 50, "failed": 5, "runs_failing_checks": 0, "feedback_bad": 0},
            v2.composition_digest: {"runs": 5, "failed": 3, "runs_failing_checks": 0, "feedback_bad": 0}}
    watcher = _watcher(fake, live)

    assert await watcher.tick(_same(db_session)) == [], "5 live runs: not enough for a check"
    row = await db_session.get(Proposal, approved["id"])
    assert row.watch["failed_checks"] == 0 and row.watch["seen_runs"] == 0

    live[v2.composition_digest] = {"runs": 12, "failed": 6, "runs_failing_checks": 1, "feedback_bad": 0}
    assert await watcher.tick(_same(db_session)) == []
    await db_session.refresh(row)
    assert row.watch["failed_checks"] == 1 and row.watch["note"].startswith("a check failed: 58% of its live runs failed")
    assert await watcher.tick(_same(db_session)) == [], "no new runs since that check: no second check on them"

    live[v2.composition_digest] = {"runs": 24, "failed": 12, "runs_failing_checks": 0, "feedback_bad": 0}
    assert await watcher.tick(_same(db_session)) == [approved["id"]]
    await db_session.refresh(row)
    v3 = (await releases(agent.id, db_session))[0]
    assert (row.watch["status"], row.watch["rolled_back_to"], v3.version) == ("rolled_back", 3, 3)
    assert (v3.composition_digest, v3.harness_version) == (v1.composition_digest, v1.harness_version), "nothing rebuilt"
    await db_session.refresh(agent)
    assert agent.composition_digest == v1.composition_digest
    assert "v1 is live again as v3" in row.watch["note"]


@pytest.mark.asyncio
async def test_a_change_no_worse_live_is_held_and_a_newer_release_ends_the_watch(db_session, owner, settings, monkeypatch):
    from prime_agent.db.models.evaluation import Proposal
    from prime_agent.services.harness_publish_service import HarnessPublisher

    fake = FakeSupervisor(candidate="ok")
    agent, approved = await _approved(db_session, owner, monkeypatch, fake)
    v1, v2 = sorted(await releases(agent.id, db_session), key=lambda r: r.version)
    live = {v1.composition_digest: {"runs": 50, "failed": 10}, v2.composition_digest: {"runs": 0, "failed": 0}}
    watcher = _watcher(fake, live)
    for n in range(1, 4):
        live[v2.composition_digest] = {"runs": 10 * n, "failed": n}
        await watcher.tick(_same(db_session))
    row = await db_session.get(Proposal, approved["id"])
    assert row.watch["status"] == "held" and row.watch["checks"] == 3 and row.watch["failed_checks"] == 0

    # a fresh watch, then a person releases another version: the watcher never undoes it
    row.watch = {"status": "watching", "previous_version": 1, "checks": 0, "failed_checks": 0, "seen_runs": 0}
    await db_session.commit()
    await HarnessPublisher(AiClient(config.get_settings(), transport=httpx.MockTransport(fake))).restore(
        agent, v1, user_id=owner.id, db=db_session)
    assert await watcher.tick(_same(db_session)) == []
    await db_session.refresh(row)
    assert row.watch["status"] == "superseded" and row.watch["note"] == "v3 was released after it"


@pytest.mark.asyncio
async def test_a_change_can_start_from_an_issue(db_session, owner, settings, monkeypatch):
    """The issue's runs join the dataset, and the reflection reads that issue first."""
    from prime_agent.db.models.evaluation import DatasetItem as Item

    fake = FakeSupervisor(candidate="ok")
    agent, ds = await _published_agent(db_session, owner, fake)
    key = f"{agent.composition_id}:check:tests_after_change:"
    plain = fake.__call__
    listed: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/traces/issues/runs":
            listed.append(request.url.params["key"])
            return httpx.Response(200, json={"issue": {"examples": []}})
        return plain(request)

    started: list = []
    monkeypatch.setattr("prime_agent.web.routes.evaluations.EvaluationService.from_settings",
                        lambda s=None: EvaluationService(config.get_settings(),
                                                         client=AiClient(config.get_settings(), transport=httpx.MockTransport(handler))))
    async with _api(db_session, owner, fake, monkeypatch, started) as api:
        api.fake, api.user = fake, owner
        other = await api.post(f"/api/v1/traces/agents/{agent.id}/proposals",
                               json={"dataset_id": ds.id, "issue_key": "someone-else:check:x:"})
        assert other.status_code == 422, "an issue of another agent"
        r = await api.post(f"/api/v1/traces/agents/{agent.id}/proposals", json={"dataset_id": ds.id, "issue_key": key})
        assert r.status_code == 202, r.text
        assert r.json()["issue_key"] == key and listed == [key]
        service = ImprovementService(config.get_settings(), client=AiClient(config.get_settings(), transport=httpx.MockTransport(handler)))
        await service.run(r.json()["id"], identity=TenantPrincipal.from_user(owner), session_factory=_same(db_session), poll_s=0)
    assert fake.reflected[-1]["focus_issue"] == key
    assert len((await db_session.execute(select(Item).where(Item.dataset_id == ds.id))).scalars().all()) == 2
