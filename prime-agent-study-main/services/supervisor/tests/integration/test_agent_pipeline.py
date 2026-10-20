"""End-to-end agent pipeline on the shipped catalog, one stage at a time.

catalog → composition → SessionPins → RuntimeSeams → githi run → policy → journal → temporal

Everything is real except the edges: a mock sandbox provider (no Docker), and a
non-placeholder test image in place of the D14 placeholder pinned in
``harness/versions/githi-v1.yaml``.
"""

from __future__ import annotations

import dataclasses
import hashlib
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from supervisor.app import SupervisorState, build_supervisor
from supervisor.catalog.core.errors import (
    CatalogError,
    DigestMismatch,
    HarnessVersionMismatch,
    NotOffered,
)
from supervisor.catalog.core.types import SessionPins
from supervisor.catalog.loading.bootstrap import (
    agent_platform_root,
    bootstrap_composition_catalog,
    default_catalog_dir,
)
from supervisor.catalog.repository import hydrate_composition_catalog
from supervisor.catalog.storage import CompositionCatalog
from supervisor.db.engine import open_db
from supervisor.harness import HarnessCatalog, load_manifest
from supervisor.main import create_app
from supervisor.seams.core.types import PromptBindings, ToolInvokeContext
from supervisor.session.manager import SessionManager
from supervisor.session.workspaces import session_workspace
from supervisor.tools import default_registry
from tests.support.session_env import TrackingProvider

HARNESS = "githi-v1"
COMPOSITION = "githi-v1"
MOCK_IMAGE = "sha256:" + hashlib.sha256(b"mock-githi-image").hexdigest()


@dataclass(frozen=True, slots=True)
class Pipeline:
    """One supervisor process (fresh in-memory state) over a shared database."""

    state: SupervisorState
    sandboxes: TrackingProvider

    @property
    def manager(self) -> SessionManager:
        return self.state.manager

    @property
    def compositions(self) -> CompositionCatalog:
        assert self.state.composition_catalog is not None
        return self.state.composition_catalog

    def digest_of(self, composition_id: str) -> str:
        return self.compositions.list_versions("composition", composition_id)[-1]

    def client(self) -> AsyncClient:
        return AsyncClient(
            transport=ASGITransport(app=create_app(state=self.state)),
            base_url="http://supervisor",
            headers={"X-Tenant-Id": "t1"},
        )


def _shipped_harness_catalog() -> HarnessCatalog:
    """Shipped manifests, with the githi image swapped for a mock (D14 placeholder)."""
    catalog = HarnessCatalog()
    for path in sorted((agent_platform_root() / "harness" / "versions").glob("*.yaml")):
        manifest = load_manifest(path.read_text(encoding="utf-8"))
        if manifest.harness_version == HARNESS:
            manifest = dataclasses.replace(manifest, image_digest=MOCK_IMAGE)
        catalog.register_sync(manifest)
    return catalog


Boot = Callable[[], Awaitable[Pipeline]]


@pytest.fixture
async def boot(tmp_path: Path) -> AsyncIterator[Boot]:
    """Start a supervisor process the way bootstrap_supervisor does; call again to restart."""
    async with open_db(f"sqlite+aiosqlite:///{tmp_path / 'pipeline.db'}") as (_, factory):

        async def start() -> Pipeline:
            compositions = bootstrap_composition_catalog(default_catalog_dir())
            await hydrate_composition_catalog(compositions, factory)  # YAL_PERSIST_CATALOG
            sandboxes = TrackingProvider()
            state = build_supervisor(
                session_factory=factory,
                catalog=_shipped_harness_catalog(),
                blob_dir=tmp_path / "blobs",
                sandbox_provider=sandboxes,
                composition_catalog=compositions,
            )
            return Pipeline(state, sandboxes)

        yield start


@pytest.fixture
async def pipeline(boot: Boot) -> Pipeline:
    return await boot()


# --- Stage 1: catalog → composition → SessionPins -------------------------------


async def test_session_pins_exact_digests_from_composition(pipeline: Pipeline) -> None:
    digest = pipeline.digest_of(COMPOSITION)
    sid = await pipeline.manager.create(
        tenant_id="t1",
        harness_version=HARNESS,
        composition_id=COMPOSITION,
        composition_digest=digest,
    )

    row = await pipeline.manager._get_session_row(sid)
    pins = SessionPins.from_dict(row.composition_pins)
    comp = pipeline.compositions.get("composition", COMPOSITION, digest)
    assert pins.composition_digest == digest
    assert pins.harness_version == HARNESS
    assert (pins.prompt_pack_id, pins.prompt_pack_digest) == (
        comp.prompt_pack.id,
        comp.prompt_pack.digest,
    )
    assert (pins.tool_set_id, pins.tool_set_digest) == (comp.tool_set.id, comp.tool_set.digest)
    assert pins.to_dict() == SessionPins.from_dict(pins.to_dict()).to_dict()  # lossless
    # The pinned image is the session's recipe; the sandbox waits for a tool that needs it.
    recipe = pipeline.manager._hands.recipe(sid)
    assert recipe is not None and recipe.image == MOCK_IMAGE
    assert pipeline.manager._hands.sandbox(sid) is None
    # The agent persists history under this id; it must be the supervisor's, or resume loses it.
    assert pipeline.manager._registry.get(sid).session_id == sid


async def test_id_only_create_pins_the_latest_offered_digest(pipeline: Pipeline) -> None:
    sid = await pipeline.manager.create(
        tenant_id="t1", harness_version=HARNESS, composition_id=COMPOSITION
    )
    row = await pipeline.manager._get_session_row(sid)
    assert row.composition_pins["composition_digest"] == pipeline.digest_of(COMPOSITION)


@pytest.mark.parametrize(
    ("override", "error"),
    [
        pytest.param(lambda p: {"composition_digest": "latest"}, CatalogError, id="floating-ref"),
        pytest.param(
            lambda p: {"composition_digest": p.digest_of("githi-tools-v1")},
            NotOffered,
            id="digest-of-another-composition",
        ),
        pytest.param(
            lambda p: {"harness_version": "kg-v1"},
            HarnessVersionMismatch,
            id="wrong-harness-version",
        ),
    ],
)
async def test_create_refuses_ambiguous_or_mismatched_pins(
    pipeline: Pipeline,
    override: Callable[[Pipeline], dict[str, str]],
    error: type[Exception],
) -> None:
    request = {
        "tenant_id": "t1",
        "harness_version": HARNESS,
        "composition_id": COMPOSITION,
        "composition_digest": pipeline.digest_of(COMPOSITION),
    } | override(pipeline)
    with pytest.raises(error):
        await pipeline.manager.create(**request)


def test_catalog_refuses_a_digest_that_does_not_match_content(pipeline: Pipeline) -> None:
    digest = pipeline.compositions.list_versions("prompt_pack", "coding-prompt")[-1]
    pinned = pipeline.compositions.get("prompt_pack", "coding-prompt", digest)
    forged = dataclasses.replace(pinned, persona="evil")  # keeps the pinned digest
    with pytest.raises(DigestMismatch):
        pipeline.compositions.offer(forged)
    assert pipeline.compositions.get("prompt_pack", pinned.name, pinned.digest).persona == pinned.persona


# --- Stage 2: user-chosen seams, durable across restarts -------------------------

WEATHER_TOOL = {
    "name": "weather",
    "description": "Current weather for a city",
    "parameters": {"type": "object", "properties": {"city": {"type": "string"}}},
    "tool_class": "pure",
}


def _registry_tool(name: str, **override: str) -> dict:
    """A tool definition as the registry publishes it, optionally re-described."""
    return default_registry()[name].definition() | override


async def _publish_user_composition(client: AsyncClient, pipeline: Pipeline) -> str:
    """A user's own githi agent: new prompt, new tool set, memory profile, ACP subagents."""

    async def publish(kind: str, body: dict) -> str:
        resp = await client.post(f"/v1/catalog/{kind}/publish", json=body)
        assert resp.status_code == 200, resp.text
        return resp.json()["digest"]

    common = {"semver": "1.0.0", "protocol_version": 1, "status": "offered"}
    prompt = await publish("prompt_pack", common | {
        "name": "my-prompt", "persona": "analyst", "vars": [],
        "sections": [{"id": "s1", "order": 1, "text": "You are MY analyst.", "complete": True}],
    })
    tools = await publish("tool_set", common | {
        "name": "my-tools",
        "definitions": {
            "read": _registry_tool("read"),
            "edit": _registry_tool("edit", description="Edit a file the user named"),
        },
    })
    memory = pipeline.compositions.list_versions("memory_profile", "default")[-1]
    return await publish("composition", common | {
        "name": "my-githi",
        "harness_version": HARNESS,
        "prompt_pack": {"id": "my-prompt", "digest": prompt},
        "tool_set": {"id": "my-tools", "digest": tools},
        "memory_profile": {"id": "default", "digest": memory},
        "plug_bindings": {"gateway_base_url": "http://gateway:8090", "acp_base_url": "http://acp:9000"},
    })


async def _create(client: AsyncClient, digest: str) -> str:
    resp = await client.post("/v1/sessions", json={
        "harness_version": HARNESS, "composition_id": "my-githi", "composition_digest": digest,
    })
    assert resp.status_code == 201, resp.text
    return resp.json()["session_id"]


def _assert_user_seams(pipeline: Pipeline, session_id: str) -> None:
    runtime = pipeline.manager._registry.get(session_id)
    seams = runtime.seams
    assert type(runtime).__name__ == "GithiRuntime"  # from the manifest, not an API default
    assert "You are MY analyst." in seams.prompt_assembler.assemble(PromptBindings()).text
    assert [t.name for t in seams.tool_provider.list_tools()] == ["edit", "read"]
    assert seams.model_gateway._base == "http://gateway:8090"
    assert seams.subagent_transport._provider("acp") is not None
    assert seams.context_memory is not None


async def test_user_composed_agent_runs_with_its_own_seams(pipeline: Pipeline) -> None:
    async with pipeline.client() as client:
        session_id = await _create(client, await _publish_user_composition(client, pipeline))
    _assert_user_seams(pipeline, session_id)


async def test_user_tools_run_from_the_registry(pipeline: Pipeline) -> None:
    async with pipeline.client() as client:
        session_id = await _create(client, await _publish_user_composition(client, pipeline))
    tools = pipeline.manager._registry.get(session_id).seams.tool_provider
    ctx = ToolInvokeContext(extra={"session_id": session_id})

    edit = next(t for t in tools.list_tools() if t.name == "edit")
    assert edit.description == "Edit a file the user named"  # the pinned text, not the registry's
    session_workspace(session_id).joinpath("notes.txt").write_text("colour: red\n")
    assert tools.invoke(ctx, "edit", {"path": "notes.txt", "old_text": "red", "new_text": "blue"}).ok
    assert tools.invoke(ctx, "read", {"file_path": "notes.txt"}).payload == "colour: blue\n"

    shell = tools.invoke(ctx, "bash", {"command": "id"})  # registered, but not in this tool set
    assert (shell.ok, shell.code) == (False, "TOOL_NOT_FOUND")


async def test_tool_set_naming_an_unimplemented_tool_is_refused(pipeline: Pipeline) -> None:
    async with pipeline.client() as client:
        resp = await client.post("/v1/catalog/tool_set/publish", json={
            "name": "weather-tools", "semver": "1.0.0", "protocol_version": 1,
            "status": "offered", "definitions": {"weather": WEATHER_TOOL},
        })
    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "INVALID_ARTIFACT"
    assert "weather" in resp.json()["detail"]["message"]


async def test_user_compositions_and_sessions_survive_restart(boot: Boot) -> None:
    before = await boot()
    async with before.client() as client:
        digest = await _publish_user_composition(client, before)
        old_session = await _create(client, digest)

    after = await boot()  # new process, same database
    async with after.client() as client:
        assert (await client.get(f"/v1/catalog/composition/my-githi/{digest}")).status_code == 200
        _assert_user_seams(after, await _create(client, digest))
    await after.manager.resume(old_session)
    _assert_user_seams(after, old_session)


async def test_tool_set_with_forged_tool_digest_is_refused(pipeline: Pipeline) -> None:
    forged = WEATHER_TOOL | {"digest": "sha256:" + "1" * 64}
    async with pipeline.client() as client:
        resp = await client.post("/v1/catalog/tool_set/publish", json={
            "name": "forged", "semver": "1.0.0", "protocol_version": 1,
            "definitions": {"weather": forged},
        })
    assert resp.status_code == 422, resp.text


def test_catalog_persistence_is_on_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    from supervisor.config import Settings

    monkeypatch.delenv("YAL_PERSIST_CATALOG", raising=False)
    assert Settings().persist_composition_catalog
    assert Settings.from_env().persist_composition_catalog
