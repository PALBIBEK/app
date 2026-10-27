"""Gated self-improvement: a change is learned from the traces, published as a candidate that is
not live, replayed on saved runs beside the live version, refused if it does worse, and made the
next version only when a person approves it."""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from uuid import uuid4

import httpx
import pytest

from prime_agent import config
from prime_agent.config import Settings, get_settings
from prime_agent.db.models.evaluation import Dataset, DatasetItem
from prime_agent.db.models.harness_release import HarnessDraft
from prime_agent.main import create_app
from prime_agent.schemas.ai_identity import TenantPrincipal
from prime_agent.services.ai_client import AiClient
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
        self.reflection = reflection if reflection is not None else {
            "proposal": {"persona": "coder", "rationale": "Runs answer before testing; say to test first.",
                         "changed": ["rules"],
                         "sections": [{"id": "intro", "text": "You are a coding agent in {cwd}."},
                                      {"id": "rules", "text": FIXED}]},
            "evidence": [{"key": "k", "title": "Tests failing after a change", "runs": 3,
                          "examples": [{"session_id": "ai-src", "run_id": "r0", "input": "fix the parser",
                                        "summary": "Changed parser.py; answered.", "reason": "tests failed"}]}],
        }
        self.published: list[tuple[str, dict]] = []
        self.sessions: dict[str, str] = {}  # ai session id -> harness version
        self.reflected: list[dict] = []

    def _good(self, sid: str) -> bool:
        return (self.candidate if "-c" in self.sessions.get(sid, "") else self.live) == "ok"

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
            return httpx.Response(200, json=self.reflection)
        if path == "/v1/sessions" and method == "POST":
            sid = uuid4().hex
            self.sessions[sid] = body["harness_version"]
            return httpx.Response(202, json={"session_id": sid, "status": "starting"})
        sid = path.split("/")[3] if path.startswith("/v1/sessions/") else ""
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
        assert p["changed"] == ["rules"] and p["rationale"].startswith("Runs answer before testing")
        assert p["candidate_prompt"]["sections"][1]["text"] == FIXED
        assert p["base_prompt"]["sections"][1]["text"] != FIXED
        assert p["evidence"][0]["title"] == "Tests failing after a change"
        assert p["evidence"][0]["examples"][0]["session_id"] is None  # not a session of the user's: not linked
        assert (p["baseline"]["label"], p["candidate"]["label"]) == ("v1", f"candidate {p['id'][:6]}")

        # the reflection read the live prompt and the person's note, never the replays
        (asked,) = fake.reflected
        assert asked["notes"] == "it skips the tests" and asked["exclude_sessions"] == []
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
        assert v2.manifest["prompt"]["sections"][1]["text"] == FIXED
        draft = await db_session.get(HarnessDraft, agent.id)
        assert draft is not None and draft.manifest["prompt"]["sections"][1]["text"] == FIXED

        again = await api.post(f"/api/v1/traces/proposals/{p['id']}/approve")
        assert again.status_code == 409

        # the next reflection never sees the sessions the first change was measured in
        await _propose(api, db_session, agent, ds, started)
        assert len(fake.reflected[1]["exclude_sessions"]) == 4


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
