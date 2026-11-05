"""The manifest builder: drafts, publishing to the supervisor's catalog, releases."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from prime_agent.config import Settings
from prime_agent.main import create_app
from prime_agent.services.ai_client import AiClient
from prime_agent.services.harness_publish_service import BROWSER_SANDBOX_IMAGE, HarnessPublisher, artifact_prefix
from prime_agent.services.harness_service import HarnessService
from prime_agent.web.dependencies import get_current_user, get_db

SETTINGS = Settings(
    environment="development",
    ai_supervisor_base_url="http://supervisor.test",
    ai_service_jwt_secret="ai-service-jwt-secret-at-least-32-bytes!!",
)
REGISTRY = [
    {"name": "read", "description": "Read a file", "tool_class": "pure", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}}},
    {"name": "bash", "description": "Run a command", "tool_class": "non_atomic", "parameters": {"type": "object", "properties": {"command": {"type": "string"}}}},
    {"name": "ls", "description": "List", "tool_class": "pure", "parameters": {"type": "object", "properties": {}}},
    {"name": "grep", "description": "Search", "tool_class": "pure", "parameters": {"type": "object", "properties": {}}},
    {"name": "write", "description": "Write", "tool_class": "idempotent", "parameters": {"type": "object", "properties": {}}},
    {"name": "edit", "description": "Edit", "tool_class": "idempotent", "parameters": {"type": "object", "properties": {}}},
    {"name": "browser_exec", "description": "Drive a browser", "tool_class": "non_atomic", "parameters": {"type": "object", "properties": {}}},
    {"name": "browser_vault", "description": "Fill a saved login", "tool_class": "non_atomic", "parameters": {"type": "object", "properties": {}}},
]
BASE = {"harness_version": "githi-v1", "runtime": "githi", "manifest": {
    "runtime": "githi", "image_digest": "img@sha256:abc",
    "recovery": {"default": "fail", "by_tool_class": {"pure": "auto_resume"}},
    "subagents": {"max_depth": 3},
}}


class FakeSupervisor:
    """Records what is published, in order; can refuse one kind."""

    def __init__(self, refuse: str | None = None) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.refuse = refuse

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/v1/tools":
            return httpx.Response(200, json=REGISTRY)
        if path == "/v1/harness_versions/githi-v1":
            return httpx.Response(200, json=BASE)
        body = json.loads(request.content) if request.content else {}
        if path.startswith("/v1/catalog/") and path.endswith("/publish"):
            kind = path.split("/")[3]
            self.calls.append((kind, body))
            if kind == self.refuse:
                return httpx.Response(422, json={"detail": {"message": f"invalid {kind}: nope", "code": "INVALID_ARTIFACT"}})
            return httpx.Response(200, json={"kind": kind, "artifact_id": body["name"], "digest": f"sha256:{kind}{len(self.calls)}", "status": "offered", "semver": body["semver"]})
        if path.startswith("/v1/catalog/") and path.endswith("/check"):
            kind = path.split("/")[3]
            self.calls.append((f"check:{kind}", body))
            ok = kind != self.refuse
            return httpx.Response(200, json={"ok": ok, "digest": f"sha256:would-be-{kind}", "checks": [
                {"check": "It reads", "ok": True, "message": "", "code": None},
                {"check": "Every field is known", "ok": ok, "message": "" if ok else "unknown field(s): x", "code": None if ok else "INVALID_ARTIFACT"},
            ]})
        if path == "/v1/harness_versions":
            self.calls.append(("harness_version", body))
            return httpx.Response(201, json={"harness_version": body["harness_version"], "runtime": body["runtime"]})
        return httpx.Response(404)


@pytest.fixture
async def owner(make_user):
    return await make_user(email="builder@example.com", name="Builder")


def _api(db, user, supervisor, monkeypatch) -> httpx.AsyncClient:
    client = AiClient(SETTINGS, transport=httpx.MockTransport(supervisor))
    monkeypatch.setattr("prime_agent.web.routes.harnesses._publisher", lambda: HarnessPublisher(client))
    app = create_app()

    async def override_db():
        yield db

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = lambda: user
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def _harness(db, owner):
    return await HarnessService.acreate(
        {"name": "builder", "model": "gateway/echo", "mode": "sticky", "user_id": owner.id, "created_by": owner.id}, db
    )


async def test_a_new_harness_drafts_as_the_shipped_coding_agent(db_session, owner, monkeypatch):
    harness = await _harness(db_session, owner)
    async with _api(db_session, owner, FakeSupervisor(), monkeypatch) as api:
        view = (await api.get(f"/api/v1/harnesses/{harness.id}/manifest")).json()
    assert view["published_version"] is None and view["unpublished_changes"] is True
    assert view["manifest"]["model"] == "gateway/echo"
    assert "bash" in view["manifest"]["tools"] and view["manifest"]["policy"]["require_approval"] == ["bash"]
    assert view["manifest"]["sandbox"]["provider"] == "local_docker"  # isolated unless configured


async def test_a_new_harness_uses_the_configured_default_sandbox(db_session, owner, monkeypatch):
    # ./dev.sh: a laptop without a Docker image still runs a new harness's commands (approved first).
    from prime_agent.config import Settings

    local = Settings(harness_default_sandbox_provider="local_process", harness_default_file_effects="danger_full_access")
    monkeypatch.setattr("prime_agent.services.harness_publish_service.get_settings", lambda: local)
    harness = await _harness(db_session, owner)
    async with _api(db_session, owner, FakeSupervisor(), monkeypatch) as api:
        view = (await api.get(f"/api/v1/harnesses/{harness.id}/manifest")).json()
    assert view["manifest"]["sandbox"]["provider"] == "local_process"
    assert view["manifest"]["sandbox"]["file_effects"] == "danger_full_access"
    assert view["manifest"]["policy"]["require_approval"] == ["bash"]


async def test_an_invalid_draft_is_refused_with_the_field_named(db_session, owner, monkeypatch):
    harness = await _harness(db_session, owner)
    async with _api(db_session, owner, FakeSupervisor(), monkeypatch) as api:
        view = (await api.get(f"/api/v1/harnesses/{harness.id}/manifest")).json()
        bad = view["manifest"] | {"policy": view["manifest"]["policy"] | {"max_steps": 0}}
        r = await api.put(f"/api/v1/harnesses/{harness.id}/manifest", json=bad)
    assert r.status_code == 422
    assert r.json()["details"][0]["field"] == "body.policy.max_steps"


async def test_publish_sends_leaves_then_the_composition_and_binds_the_harness(db_session, owner, monkeypatch):
    harness = await _harness(db_session, owner)
    supervisor = FakeSupervisor()
    async with _api(db_session, owner, supervisor, monkeypatch) as api:
        view = (await api.get(f"/api/v1/harnesses/{harness.id}/manifest")).json()
        manifest = view["manifest"]
        manifest["model"] = "gateway/githi"
        manifest["sandbox"]["provider"] = "local_docker"
        manifest["policy"]["max_steps"] = 12
        manifest["policy"]["max_run_minutes"] = 20
        manifest["policy"]["hard_run_minutes"] = 30
        manifest["policy"]["max_tokens_per_session"] = 2_000_000
        manifest["policy"]["max_minutes_per_session"] = 120
        assert (await api.put(f"/api/v1/harnesses/{harness.id}/manifest", json=manifest)).status_code == 200
        r = await api.post(f"/api/v1/harnesses/{harness.id}/publish")
        again = await api.post(f"/api/v1/harnesses/{harness.id}/publish")
        listed = await api.get(f"/api/v1/harnesses/{harness.id}/releases")
    assert r.status_code == 200, r.text
    kinds = [k for k, _ in supervisor.calls]
    assert kinds == ["prompt_pack", "tool_set", "sandbox_profile", "memory_profile", "harness_version", "composition"]
    bodies = dict(supervisor.calls)
    prefix = artifact_prefix(harness.id)
    assert {b["name"] for k, b in supervisor.calls if k != "harness_version"} == {
        f"{prefix}-prompt", f"{prefix}-tools", f"{prefix}-sandbox", f"{prefix}-memory", prefix,
    }
    assert bodies["prompt_pack"]["vars"] == []  # the runtime says where the agent works
    assert set(bodies["tool_set"]["tools"]) == set(manifest["tools"])
    hv = bodies["harness_version"]
    assert hv["harness_version"] == f"{prefix}-r1"
    assert hv["loop"]["max_steps"] == 12 and hv["approvals"]["require_approval"] == ["bash"]
    assert hv["loop"]["max_run_seconds"] == 1200  # pinned with the release
    assert "model_retries" not in hv["loop"]  # left at its default: the digest is unchanged
    assert hv["loop"]["hard_run_seconds"] == 1800  # pi-temporal's hard deadline
    assert hv["budget"]["max_tokens_per_session"] == 2_000_000
    assert hv["budget"]["max_seconds_per_session"] == 7200  # pi-temporal's session time bound
    assert hv["tools"]["bash"] == {"class": "non_atomic"} and hv["image_digest"] == "img@sha256:abc"
    comp = bodies["composition"]
    assert comp["plug_bindings"]["model_route"] == "githi"  # gateway/githi → the route
    assert comp["harness_version"] == hv["harness_version"]
    assert comp["sandbox_profile"]["digest"].startswith("sha256:sandbox_profile")

    release = r.json()["release"]
    assert r.json()["created"] is True and release["version"] == 1
    assert again.json()["created"] is False and len(supervisor.calls) == 6  # unchanged: nothing sent
    assert [x["version"] for x in listed.json()] == [1]
    await db_session.refresh(harness)
    assert harness.bind_status == "bound" and harness.composition_id == prefix
    assert harness.harness_version == f"{prefix}-r1" and harness.model == "gateway/githi"
    assert harness.published_tools == manifest["tools"]  # what lists and cards show


async def _publish_sandbox(db, owner, monkeypatch, tools: list[str], sandbox: dict) -> dict[str, dict]:
    """Publish a harness with ``tools`` and ``sandbox`` settings; what it sent, by kind."""
    harness = await _harness(db, owner)
    supervisor = FakeSupervisor()
    async with _api(db, owner, supervisor, monkeypatch) as api:
        manifest = (await api.get(f"/api/v1/harnesses/{harness.id}/manifest")).json()["manifest"]
        manifest |= {"tools": tools, "policy": manifest["policy"] | {"require_approval": []}}
        manifest["sandbox"] |= sandbox
        assert (await api.put(f"/api/v1/harnesses/{harness.id}/manifest", json=manifest)).status_code == 200
        assert (await api.post(f"/api/v1/harnesses/{harness.id}/publish")).status_code == 200
    return dict(supervisor.calls)


async def test_a_browser_agent_publishes_a_sandbox_that_has_a_browser_and_reaches_the_web(db_session, owner, monkeypatch):
    sent = await _publish_sandbox(
        db_session, owner, monkeypatch, ["browser_exec", "browser_vault"],
        {"provider": "local_docker", "network": {"mode": "open"}, "browser": "sandbox-chromium"},
    )

    profile = sent["sandbox_profile"]
    assert profile["network"] == {"mode": "open"} and profile["browser"] == "sandbox-chromium"
    assert profile["image_digest"] == sent["harness_version"]["image_digest"] == BROWSER_SANDBOX_IMAGE


async def test_a_remote_browser_reaches_only_its_host_and_needs_no_browser_image(db_session, owner, monkeypatch):
    sent = await _publish_sandbox(
        db_session, owner, monkeypatch, ["browser_exec"],
        {"provider": "local_docker", "network": {"mode": "allow", "hosts": ["cdp.browser.example"]}, "browser": "remote-cdp"},
    )

    profile = sent["sandbox_profile"]
    assert profile["network"] == {"mode": "allow", "hosts": ["cdp.browser.example"]} and profile["browser"] == "remote-cdp"
    assert profile["image_digest"] == "img@sha256:abc", "the base harness's image"


async def test_a_harness_without_browser_tools_keeps_the_base_image_and_names_no_network(db_session, owner, monkeypatch):
    sent = await _publish_sandbox(db_session, owner, monkeypatch, ["read", "bash"], {"provider": "local_docker"})

    profile = sent["sandbox_profile"]
    assert profile["image_digest"] == "img@sha256:abc"
    assert "network" not in profile and "browser" not in profile, "unset fields stay out, so digests are unchanged"


def test_the_browser_image_is_the_shipped_browser_profiles() -> None:
    shipped = Path(__file__).parents[2] / "prime-agent-study-main/harness/sandbox_profiles/githi-browser-docker-v1.json"
    assert json.loads(shipped.read_text())["image_digest"] == BROWSER_SANDBOX_IMAGE


async def test_a_refusal_names_the_manifest_section(db_session, owner, monkeypatch):
    harness = await _harness(db_session, owner)
    async with _api(db_session, owner, FakeSupervisor(refuse="sandbox_profile"), monkeypatch) as api:
        r = await api.post(f"/api/v1/harnesses/{harness.id}/publish")
        releases = await api.get(f"/api/v1/harnesses/{harness.id}/releases")
    assert r.status_code == 422
    assert r.json()["detail"]["section"] == "sandbox"
    assert "nope" in r.json()["detail"]["message"]
    assert releases.json() == []  # nothing recorded, nothing bound
    await db_session.refresh(harness)
    assert harness.bind_status == "unbound"


async def test_a_tool_the_supervisor_cannot_run_is_refused_before_anything_is_published(db_session, owner, monkeypatch):
    harness = await _harness(db_session, owner)
    supervisor = FakeSupervisor()
    async with _api(db_session, owner, supervisor, monkeypatch) as api:
        manifest = (await api.get(f"/api/v1/harnesses/{harness.id}/manifest")).json()["manifest"]
        manifest["tools"].append("teleport")
        await api.put(f"/api/v1/harnesses/{harness.id}/manifest", json=manifest)
        r = await api.post(f"/api/v1/harnesses/{harness.id}/publish")
    assert r.status_code == 422 and r.json()["detail"]["section"] == "tools"
    assert supervisor.calls == []


async def test_an_approval_that_cannot_be_answered_within_the_hard_limit_is_refused(db_session, owner, monkeypatch):
    """The run's hard limit counts the wait for an approval: a wait as long as the limit
    would stop every run that asks before its answer could come."""
    harness = await _harness(db_session, owner)
    supervisor = FakeSupervisor()
    async with _api(db_session, owner, supervisor, monkeypatch) as api:
        manifest = (await api.get(f"/api/v1/harnesses/{harness.id}/manifest")).json()["manifest"]
        assert manifest["policy"]["require_approval"] == ["bash"]
        manifest["policy"] |= {"approval_timeout_s": 300, "hard_run_minutes": 5}
        await api.put(f"/api/v1/harnesses/{harness.id}/manifest", json=manifest)
        checked = await api.post(f"/api/v1/harnesses/{harness.id}/check")
        r = await api.post(f"/api/v1/harnesses/{harness.id}/publish")
    failed = {(c["section"], c["code"]) for c in checked.json()["checks"] if not c["ok"]}
    assert failed == {("policy", "APPROVAL_PAST_DEADLINE")}
    assert r.status_code == 422 and r.json()["detail"]["section"] == "policy"
    assert "must be shorter than hard_run_minutes" in r.json()["detail"]["message"]
    assert not [k for k, _ in supervisor.calls if not k.startswith("check:")], "nothing published"


async def test_another_users_harness_is_not_found(db_session, owner, make_user, monkeypatch):
    harness = await _harness(db_session, owner)
    other = await make_user(email="other-builder@example.com", name="Other")
    async with _api(db_session, other, FakeSupervisor(), monkeypatch) as api:
        for method, path in (("get", "manifest"), ("post", "publish"), ("post", "check"), ("get", "releases")):
            r = await getattr(api, method)(f"/api/v1/harnesses/{harness.id}/{path}")
            assert r.status_code == 404, (path, r.status_code)


async def test_check_runs_every_publish_check_and_publishes_nothing(db_session, owner, monkeypatch):
    harness = await _harness(db_session, owner)
    supervisor = FakeSupervisor(refuse="sandbox_profile")
    async with _api(db_session, owner, supervisor, monkeypatch) as api:
        manifest = (await api.get(f"/api/v1/harnesses/{harness.id}/manifest")).json()["manifest"]
        manifest["tools"].append("teleport")
        await api.put(f"/api/v1/harnesses/{harness.id}/manifest", json=manifest)
        r = await api.post(f"/api/v1/harnesses/{harness.id}/check")
        releases = await api.get(f"/api/v1/harnesses/{harness.id}/releases")
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["ok"] is False
    failed = {(c["section"], c["code"]) for c in out["checks"] if not c["ok"]}
    assert failed == {("tools", "UNKNOWN_TOOL"), ("sandbox", "INVALID_ARTIFACT")}  # every problem at once
    kinds = [k for k, _ in supervisor.calls]
    assert kinds == ["check:prompt_pack", "check:tool_set", "check:sandbox_profile", "check:memory_profile", "check:composition"]
    comp = dict(supervisor.calls)["check:composition"]
    assert comp["sandbox_profile"]["digest"] == "sha256:would-be-sandbox_profile"  # the digests it would pin
    assert "teleport" not in dict(supervisor.calls)["check:tool_set"]["tools"]
    assert releases.json() == []  # nothing published, nothing registered, nothing bound
    await db_session.refresh(harness)
    assert harness.bind_status == "unbound"


async def test_a_clean_draft_checks_ok(db_session, owner, monkeypatch):
    harness = await _harness(db_session, owner)
    async with _api(db_session, owner, FakeSupervisor(), monkeypatch) as api:
        r = await api.post(f"/api/v1/harnesses/{harness.id}/check")
    assert r.status_code == 200 and r.json()["ok"] is True
    assert {c["section"] for c in r.json()["checks"]} >= {"prompt", "tools", "sandbox", "memory", "policy", "model"}


def test_learnt_notes_are_kept_apart_from_the_persons_sections() -> None:
    """Prime Agent's base prompt and supplemental notes: the notes are their own list, sent
    after the person's sections; a manifest without notes stores as it always did."""
    from pydantic import ValidationError

    from prime_agent.schemas.harness_manifest import HarnessManifest, default_manifest, learned_text, manifest_body

    plain = default_manifest()
    assert "learned" not in manifest_body(plain)["prompt"], "older releases compare unchanged"
    body = manifest_body(plain)
    body["prompt"]["learned"] = [{"id": "note-00001", "text": "Run the tests first."}]
    noted = HarnessManifest.model_validate(body)
    assert learned_text(noted.prompt.learned or []) == "Learnt from earlier runs:\n- Run the tests first."
    body["prompt"]["learned"] = []
    assert HarnessManifest.model_validate(body).prompt.learned is None
    for bad in ({"id": "rules", "text": "x"}, {"id": "note-1", "text": "x"}):
        body["prompt"]["learned"] = [bad]
        with pytest.raises(ValidationError):
            HarnessManifest.model_validate(body)
    body["prompt"]["learned"] = None
    body["prompt"]["sections"].append({"id": "learned", "text": "mine"})
    with pytest.raises(ValidationError, match="kept for the notes"):
        HarnessManifest.model_validate(body)
