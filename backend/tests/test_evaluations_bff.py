"""Datasets and experiments: save a run, replay the dataset on a version in fresh sessions,
read back how each item went, compare two versions; never approve; only ever the user's own."""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
import pytest

from prime_agent.config import Settings, get_settings
from prime_agent.db.models.evaluation import Dataset, Experiment
from prime_agent.db.models.harness_release import HarnessRelease
from prime_agent.db.models.session_link import SessionLink
from prime_agent.main import create_app
from prime_agent.schemas.ai_identity import TenantPrincipal
from prime_agent.services.ai_client import AiClient
from prime_agent.services.evaluation_service import EvaluationService
from prime_agent.services.harness_service import HarnessService
from prime_agent.web.dependencies import get_current_user, get_db

V1 = "sha256:" + "1" * 64
V2 = "sha256:" + "2" * 64


@pytest.fixture
def settings(monkeypatch):
    s = Settings(
        environment="development",
        secret_key="user-secret-key-also-at-least-32-bytes!!",
        ai_supervisor_base_url="http://supervisor.test",
        ai_service_jwt_secret="ai-service-jwt-secret-at-least-32-bytes!!",
    )
    get_settings.cache_clear()
    for target in (
        "prime_agent.config.get_settings",
        "prime_agent.config.settings.get_settings",
        "prime_agent.services.ai_client.get_settings",
        "prime_agent.security.service_jwt.get_settings",
        "prime_agent.web.routes.evaluations.get_settings",
    ):
        monkeypatch.setattr(target, lambda: s)
    yield s
    get_settings.cache_clear()


@pytest.fixture
async def owner(make_user):
    return await make_user(email="evals@example.com", name="Evals")


@pytest.fixture
async def agent(db_session, owner):
    harness = await HarnessService.acreate(
        {"name": "reviewer", "model": "gateway/echo", "mode": "sticky", "user_id": owner.id, "created_by": owner.id},
        db_session,
    )
    harness.bind_status, harness.composition_id, harness.composition_digest = "bound", "h-reviewer", V2
    for version, digest in ((1, V1), (2, V2)):
        db_session.add(HarnessRelease(harness_id=harness.id, version=version, manifest={}, composition_id="h-reviewer",
                                      composition_digest=digest, harness_version=f"h-reviewer-r{version}",
                                      runtime="githi", created_by=owner.id))
    await db_session.flush()
    return harness


class FakeSupervisor:
    """Sessions, messages and runs as the supervisor answers them; each replay ends as told."""

    def __init__(self, outcome: dict[str, str]) -> None:
        self.outcome = outcome  # composition digest -> "ok" | "fail" | "approval"
        self.sessions: dict[str, str] = {}  # ai session id -> digest
        self.cancelled: list[str] = []
        self.ended: list[str] = []
        self.created: list[dict] = []

    def trace(self, sid: str, run_id: str) -> dict:
        digest = self.sessions.get(sid, V1)
        good = self.outcome.get(digest) == "ok"
        scores = [{"id": "c", "span_id": None, "name": "finished", "topic": None, "source": "check",
                   "label": "pass" if good else "fail", "value": None, "reason": None, "comment": None,
                   "evaluator": "checks@1", "principal_id": None, "created_ms": 1},
                  {"id": "j", "span_id": None, "name": "answers", "topic": "answers", "source": "judge",
                   "label": "pass" if good else "fail", "value": None, "reason": "judged", "comment": None,
                   "evaluator": "judge:answers@v1", "principal_id": None, "created_ms": 1}]
        return {"session_id": sid, "run_id": run_id, "trace_id": "t" * 32, "composition_digest": digest,
                "summary": "Answered." if good else "Failed: boom.",
                "rollup": {"status": "ok" if good else "error", "input": "fix the test", "cost": 0.02 if good else 0.05,
                           "duration_ms": 1000},
                "spans": [{"id": "root", "parent_id": None, "kind": "agent", "name": "x", "attrs": {}}], "scores": scores}

    def handler(self, request: httpx.Request) -> httpx.Response:
        path, method = request.url.path, request.method
        if path == "/v1/sessions" and method == "POST":
            body = json.loads(request.content)
            self.created.append(body)
            sid = uuid4().hex
            self.sessions[sid] = body["composition_digest"]
            return httpx.Response(202, json={"session_id": sid, "status": "starting"})
        sid = path.split("/")[3] if path.startswith("/v1/sessions/") else ""
        if path.endswith("/startup"):
            return httpx.Response(200, json={"status": "ready", "worker_available": True})
        if path.endswith("/messages"):
            return httpx.Response(202, json={"turn_id": f"run-{sid[:6]}", "status": "accepted"})
        if "/runs/" in path and path.endswith("/cancel"):
            self.cancelled.append(sid)
            return httpx.Response(200, json={"status": "cancelled"})
        if "/runs/" in path and path.endswith("/trace"):
            return httpx.Response(200, json=self.trace(sid, path.split("/")[5]))
        if "/runs/" in path and "/trace/spans/" in path:
            return httpx.Response(200, json={"id": "root", "detail": {"input": "fix the test, the full message"}})
        if "/runs/" in path:
            if self.outcome.get(self.sessions.get(sid, "")) == "approval":
                return httpx.Response(202, json={"status": "requires_action", "pending_approval_id": "ap1"})
            return httpx.Response(200, json={"status": "completed"})
        if method == "DELETE":
            self.ended.append(sid)
            return httpx.Response(204)
        return httpx.Response(404, json={"detail": f"unexpected {method} {path}"})


def _api(db_session, user, settings, monkeypatch, fake: FakeSupervisor, launched: list) -> httpx.AsyncClient:
    def service(s=None) -> EvaluationService:
        return EvaluationService(settings, client=AiClient(settings, transport=httpx.MockTransport(fake.handler)))

    monkeypatch.setattr("prime_agent.web.routes.evaluations.EvaluationService.from_settings", service)
    monkeypatch.setattr("prime_agent.web.routes.evaluations.launch", lambda svc, exp_id, identity: launched.append((svc, exp_id, identity)))
    app = create_app()

    async def override_db():
        yield db_session

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = lambda: user
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def _source_run(db_session, owner, agent) -> SessionLink:
    link = SessionLink(id=str(uuid4()), user_id=owner.id, harness_id=agent.id, ai_session_id="ai-src", tenant_id=owner.id,
                       harness_version="h-reviewer-r1", runtime="githi", composition_id="h-reviewer",
                       composition_digest=V1, status="active", created_at=datetime.now(UTC), updated_at=datetime.now(UTC))
    db_session.add(link)
    await db_session.flush()
    return link


@pytest.mark.asyncio
async def test_a_dataset_is_replayed_on_two_versions_and_compared(db_session, owner, agent, settings, monkeypatch):
    fake = FakeSupervisor({V1: "fail", V2: "ok"})
    launched: list = []
    src = await _source_run(db_session, owner, agent)

    @asynccontextmanager
    async def same_session():
        yield db_session

    async with _api(db_session, owner, settings, monkeypatch, fake, launched) as api:
        ds = (await api.post(f"/api/v1/traces/agents/{agent.id}/datasets", json={"name": "regressions"})).json()
        assert (await api.post(f"/api/v1/traces/agents/{agent.id}/datasets", json={"name": "regressions"})).status_code == 409
        item = await api.post(f"/api/v1/traces/datasets/{ds['id']}/items",
                              json={"session_id": src.id, "run_id": "r1", "expected": "fixes the parser"})
        assert item.status_code == 201, item.text
        assert item.json()["input"] == "fix the test, the full message" and item.json()["source_version"] == 1
        dup = await api.post(f"/api/v1/traces/datasets/{ds['id']}/items", json={"session_id": src.id, "run_id": "r1"})
        assert dup.status_code == 409

        ids = {}
        for version in (1, 2):
            started = await api.post(f"/api/v1/traces/datasets/{ds['id']}/experiments", json={"version": version})
            assert started.status_code == 202, started.text
            assert started.json()["label"] == f"v{version}" and started.json()["status"] == "queued"
            svc, exp_id, identity = launched[-1]
            await svc.run_experiment(exp_id, identity=identity, session_factory=same_session, poll_s=0)
            ids[version] = exp_id

        detail = (await api.get(f"/api/v1/traces/experiments/{ids[2]}")).json()
        assert detail["experiment"]["status"] == "done"
        (result,) = detail["results"]
        assert result["status"] == "done" and result["run_status"] == "ok" and result["summary"] == "Answered."
        assert [(v["name"], v["label"]) for v in result["verdicts"]] == [("finished", "pass"), ("answers", "pass")]
        stats = detail["experiment"]["stats"]
        assert (stats["answered"], stats["judge_pass"], stats["checks_failed"]) == (1, 1, 0)

        cmp = (await api.get("/api/v1/traces/experiments/compare", params={"a": ids[1], "b": ids[2]})).json()
        assert (cmp["better"], cmp["worse"]) == (1, 0) and cmp["rows"][0]["change"] == "better"

        overview = (await api.get(f"/api/v1/traces/datasets/{ds['id']}")).json()
        assert [e["label"] for e in overview["experiments"]] == ["v2", "v1"] and len(overview["items"]) == 1

    assert [c["composition_digest"] for c in fake.created] == [V1, V2]  # each replay pinned to its version
    assert len(fake.ended) == 2  # every replay's session is ended


@pytest.mark.asyncio
async def test_a_replay_that_stops_for_an_approval_is_cancelled_never_approved(db_session, owner, agent, settings, monkeypatch):
    fake = FakeSupervisor({V2: "approval"})
    launched: list = []
    src = await _source_run(db_session, owner, agent)

    @asynccontextmanager
    async def same_session():
        yield db_session

    async with _api(db_session, owner, settings, monkeypatch, fake, launched) as api:
        ds = (await api.post(f"/api/v1/traces/agents/{agent.id}/datasets", json={"name": "risky"})).json()
        await api.post(f"/api/v1/traces/datasets/{ds['id']}/items", json={"session_id": src.id, "run_id": "r1"})
        await api.post(f"/api/v1/traces/datasets/{ds['id']}/experiments", json={})
        svc, exp_id, identity = launched[-1]
        await svc.run_experiment(exp_id, identity=identity, session_factory=same_session, poll_s=0)
        detail = (await api.get(f"/api/v1/traces/experiments/{exp_id}")).json()
    (result,) = detail["results"]
    assert result["status"] == "needs_approval" and "never approve" in result["error"]
    assert len(fake.cancelled) == 1 and detail["experiment"]["stats"]["needs_approval"] == 1


@pytest.mark.asyncio
async def test_an_experiment_needs_saved_runs_and_one_at_a_time(db_session, owner, agent, settings, monkeypatch):
    fake = FakeSupervisor({})
    launched: list = []
    src = await _source_run(db_session, owner, agent)
    async with _api(db_session, owner, settings, monkeypatch, fake, launched) as api:
        ds = (await api.post(f"/api/v1/traces/agents/{agent.id}/datasets", json={"name": "d"})).json()
        empty = await api.post(f"/api/v1/traces/datasets/{ds['id']}/experiments", json={})
        assert empty.status_code == 409 and empty.json()["detail"]["error_code"] == "EMPTY_DATASET"
        await api.post(f"/api/v1/traces/datasets/{ds['id']}/items", json={"session_id": src.id, "run_id": "r1"})
        assert (await api.post(f"/api/v1/traces/datasets/{ds['id']}/experiments", json={"version": 9})).status_code == 409
        assert (await api.post(f"/api/v1/traces/datasets/{ds['id']}/experiments", json={})).status_code == 202
        busy = await api.post(f"/api/v1/traces/datasets/{ds['id']}/experiments", json={})
        assert busy.json()["detail"]["error_code"] == "EXPERIMENT_RUNNING"
        assert (await api.post(f"/api/v1/traces/experiments/{launched[-1][1]}/cancel")).status_code == 204


@pytest.mark.asyncio
async def test_a_stranger_sees_none_of_it(db_session, owner, agent, settings, monkeypatch, make_user):
    fake = FakeSupervisor({})
    launched: list = []
    src = await _source_run(db_session, owner, agent)
    async with _api(db_session, owner, settings, monkeypatch, fake, launched) as api:
        ds = (await api.post(f"/api/v1/traces/agents/{agent.id}/datasets", json={"name": "mine"})).json()
        await api.post(f"/api/v1/traces/datasets/{ds['id']}/items", json={"session_id": src.id, "run_id": "r1"})
        await api.post(f"/api/v1/traces/datasets/{ds['id']}/experiments", json={})
    exp_id = launched[-1][1]
    stranger = await make_user(email="stranger-evals@example.com", name="S")
    async with _api(db_session, stranger, settings, monkeypatch, fake, launched) as api:
        assert (await api.get(f"/api/v1/traces/agents/{agent.id}/datasets")).status_code == 404
        assert (await api.get(f"/api/v1/traces/datasets/{ds['id']}")).status_code == 404
        assert (await api.post(f"/api/v1/traces/datasets/{ds['id']}/items", json={"session_id": src.id, "run_id": "r1"})).status_code == 404
        assert (await api.get(f"/api/v1/traces/experiments/{exp_id}")).status_code == 404
        assert (await api.delete(f"/api/v1/traces/datasets/{ds['id']}")).status_code == 404


@pytest.mark.asyncio
async def test_an_experiment_cut_off_by_a_restart_reads_as_failed(db_session, owner, agent, settings):
    ds = Dataset(user_id=owner.id, harness_id=agent.id, name="old")
    db_session.add(ds)
    await db_session.flush()
    exp = Experiment(dataset_id=ds.id, user_id=owner.id, harness_id=agent.id, label="v1", harness_version="x",
                     runtime="githi", status="running", heartbeat_at=datetime.now(UTC) - timedelta(hours=1))
    db_session.add(exp)
    await db_session.flush()
    service = EvaluationService(settings, client=AiClient(settings, transport=httpx.MockTransport(lambda r: httpx.Response(404))))
    detail = await service.experiment(identity=TenantPrincipal.from_user(owner), user_id=owner.id, experiment_id=exp.id, db=db_session)
    assert detail.experiment.status == "failed" and "restarted" in (detail.experiment.error or "")
