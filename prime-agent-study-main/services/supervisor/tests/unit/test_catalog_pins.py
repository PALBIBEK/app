"""Catalog-pins — digests, CompositionCatalog, session pin wiring (QUEUE step 2)."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest


def _supervisor_src() -> Path:
    return Path(__file__).resolve().parents[2] / "src" / "supervisor"


# --- helpers ---


def _sample_prompt_pack(
    *,
    name: str = "coding-prompt",
    persona: str | None = "coder",
    status: str = "offered",
):
    from supervisor.catalog.types import PromptPack, PromptSection

    return PromptPack(
        name=name,
        semver="1.0.0",
        digest="",
        protocol_version=1,
        status=status,  # type: ignore[arg-type]
        persona=persona,
        sections=(
            PromptSection(id="intro", order=1, text="You are a coding agent.", complete=True),
            PromptSection(id="rules", order=2, text="Prefer small diffs.", complete=True),
        ),
        vars=("cwd",),
    )


def _sample_tool_set(
    *,
    name: str = "coding-tools",
    tool_names: tuple[str, ...] = ("echo",),
    status: str = "offered",
):
    from supervisor.catalog.digests import content_digest
    from supervisor.catalog.types import ToolDefinition, ToolSet

    definitions = {}
    tools = {}
    for tn in tool_names:
        body = {
            "name": tn,
            "description": f"{tn} tool",
            "parameters": {"type": "object", "properties": {"text": {"type": "string"}}},
        }
        d = content_digest(body)
        definitions[tn] = ToolDefinition(
            name=tn,
            description=body["description"],
            parameters=body["parameters"],
            digest=d,
        )
        tools[tn] = d
    return ToolSet(
        name=name,
        semver="1.0.0",
        digest="",
        protocol_version=1,
        status=status,  # type: ignore[arg-type]
        tools=tools,
        definitions=definitions,
    )


def _offer_composition(cat, *, pack=None, tools=None, name: str = "coding-v1", status: str = "offered"):
    from supervisor.catalog.types import AgentComposition, DigestRef

    pack = pack or cat.offer(_sample_prompt_pack())
    tools = tools or cat.offer(_sample_tool_set())
    comp = AgentComposition(
        name=name,
        semver="1.0.0",
        digest="",
        protocol_version=1,
        status=status,  # type: ignore[arg-type]
        harness_version="coding-v1",
        prompt_pack=DigestRef(id=pack.name, digest=pack.digest),
        tool_set=DigestRef(id=tools.name, digest=tools.digest),
    )
    return cat.offer(comp), pack, tools


# --- HP-1 ---


def test_hp1_canonical_digest_stable_across_encodes() -> None:
    from supervisor.catalog.digests import canonical_json_bytes, content_digest

    body = {"name": "pack-a", "semver": "1.0.0", "persona": None, "sections": [], "vars": []}
    d1 = content_digest(body)
    d2 = content_digest(dict(reversed(list(body.items()))))
    assert d1.startswith("sha256:")
    assert len(d1) == len("sha256:") + 64
    assert d1 == d2
    assert canonical_json_bytes(body) == canonical_json_bytes(
        {"vars": [], "sections": [], "persona": None, "semver": "1.0.0", "name": "pack-a"}
    )


# --- HP-2 ---


def test_hp2_offer_prompt_pack_round_trip() -> None:
    from supervisor.catalog.store import CompositionCatalog

    cat = CompositionCatalog()
    pack = _sample_prompt_pack()
    offered = cat.offer(pack)
    got = cat.get("prompt_pack", offered.name, offered.digest)
    assert got.name == pack.name
    assert got.persona == pack.persona
    assert len(got.sections) == 2
    assert got.digest == offered.digest
    assert got.digest.startswith("sha256:")


# --- HP-3 ---


def test_hp3_toolset_list_tools_via_catalog_provider() -> None:
    from supervisor.catalog.providers.catalog_tools import CatalogToolProvider
    from supervisor.catalog.store import CompositionCatalog

    cat = CompositionCatalog()
    ts = cat.offer(_sample_tool_set())
    provider = CatalogToolProvider(ts)
    names = [t.name for t in provider.list_tools()]
    assert names == ["echo"]
    assert provider.list_tools()[0].parameters["type"] == "object"


# --- HP-4 ---


def test_hp4_resolve_composition_to_session_pins() -> None:
    from supervisor.catalog.resolve import resolve_composition
    from supervisor.catalog.store import CompositionCatalog

    cat = CompositionCatalog()
    comp, pack, tools = _offer_composition(cat)
    pins = resolve_composition(cat, composition_id=comp.name)
    assert pins.composition_id == comp.name
    assert pins.composition_digest == comp.digest
    assert pins.prompt_pack_digest == pack.digest
    assert pins.tool_set_digest == tools.digest
    assert pins.harness_version == "coding-v1"


# --- HP-5 ---


def test_hp5_bind_runtime_seams_non_none_providers() -> None:
    from supervisor.catalog.binder import bind_runtime_seams
    from supervisor.catalog.resolve import resolve_composition
    from supervisor.catalog.store import CompositionCatalog
    from supervisor.seams.prompt_assembler import PromptAssembler
    from supervisor.seams.tool_provider import ToolProvider

    cat = CompositionCatalog()
    comp, _, _ = _offer_composition(cat)
    pins = resolve_composition(cat, composition_id=comp.name)
    seams = bind_runtime_seams(pins, cat)
    assert seams.prompt_assembler is not None
    assert seams.tool_provider is not None
    assert isinstance(seams.prompt_assembler, PromptAssembler)
    assert isinstance(seams.tool_provider, ToolProvider)


# --- HP-7 ---


def test_hp7_catalog_prompt_assemble_deterministic() -> None:
    from supervisor.catalog.providers.catalog_prompt import CatalogPromptAssembler
    from supervisor.catalog.store import CompositionCatalog
    from supervisor.seams.types import PromptBindings

    cat = CompositionCatalog()
    pack = cat.offer(_sample_prompt_pack())
    asm = CatalogPromptAssembler(pack)
    a = asm.assemble(PromptBindings(cwd="/tmp"))
    b = asm.assemble(PromptBindings(cwd="/tmp"))
    assert "coding agent" in a.text
    assert "Prefer small diffs" in a.text
    assert a.text == b.text
    assert a.content_digest == b.content_digest


# --- FL-1 ---


def test_fl1_digest_mismatch_on_get() -> None:
    from dataclasses import replace

    from supervisor.catalog.errors import DigestMismatch
    from supervisor.catalog.store import CompositionCatalog

    cat = CompositionCatalog()
    pack = cat.offer(_sample_prompt_pack())
    key = ("prompt_pack", pack.name, pack.digest)
    corrupted = replace(pack, persona="tampered")
    cat._by_key[key] = corrupted
    with pytest.raises(DigestMismatch) as exc:
        cat.get("prompt_pack", pack.name, pack.digest)
    assert exc.value.code == "DIGEST_MISMATCH"


# --- FL-2 ---


def test_fl2_digest_missing() -> None:
    from supervisor.catalog.errors import DigestMissing
    from supervisor.catalog.store import CompositionCatalog

    cat = CompositionCatalog()
    with pytest.raises(DigestMissing) as exc:
        cat.get("prompt_pack", "missing", "sha256:" + "0" * 64)
    assert exc.value.code == "DIGEST_MISSING"


# --- FL-3 ---


def test_fl3_floating_ref_refused() -> None:
    from supervisor.catalog.errors import FloatingRef
    from supervisor.catalog.resolve import resolve_composition
    from supervisor.catalog.store import CompositionCatalog

    cat = CompositionCatalog()
    with pytest.raises(FloatingRef) as exc:
        resolve_composition(cat, composition_id="latest")
    assert exc.value.code == "FLOATING_REF"


# --- FL-4 ---


def test_fl4_draft_prompt_pack_not_offered() -> None:
    from supervisor.catalog.errors import NotOffered
    from supervisor.catalog.resolve import resolve_composition
    from supervisor.catalog.store import CompositionCatalog
    from supervisor.catalog.types import AgentComposition, DigestRef

    cat = CompositionCatalog()
    draft = cat.offer(_sample_prompt_pack(status="draft"))
    tools = cat.offer(_sample_tool_set())
    comp = cat.offer(
        AgentComposition(
            name="bad",
            semver="1.0.0",
            digest="",
            protocol_version=1,
            status="offered",
            harness_version="coding-v1",
            prompt_pack=DigestRef(id=draft.name, digest=draft.digest),
            tool_set=DigestRef(id=tools.name, digest=tools.digest),
        )
    )
    with pytest.raises(NotOffered) as exc:
        resolve_composition(cat, composition_id=comp.name)
    assert exc.value.code == "NOT_OFFERED"


# --- FL-5 ---


def test_fl5_composition_missing_tool_set() -> None:
    from supervisor.catalog.errors import CompositionIncomplete
    from supervisor.catalog.resolve import resolve_composition
    from supervisor.catalog.store import CompositionCatalog
    from supervisor.catalog.types import AgentComposition, DigestRef

    cat = CompositionCatalog()
    pack = cat.offer(_sample_prompt_pack())
    comp = cat.offer(
        AgentComposition(
            name="incomplete",
            semver="1.0.0",
            digest="",
            protocol_version=1,
            status="offered",
            harness_version="coding-v1",
            prompt_pack=DigestRef(id=pack.name, digest=pack.digest),
            tool_set=DigestRef(id="", digest=""),
        )
    )
    with pytest.raises(CompositionIncomplete) as exc:
        resolve_composition(cat, composition_id=comp.name)
    assert exc.value.code == "COMPOSITION_INCOMPLETE"


# --- FL-6 ---


def test_fl6_refuse_in_place_body_mutation() -> None:
    from supervisor.catalog.errors import ImmutableArtifact
    from supervisor.catalog.store import CompositionCatalog

    cat = CompositionCatalog()
    pack = cat.offer(_sample_prompt_pack())
    mutated = _sample_prompt_pack(persona="hacker")
    with pytest.raises(ImmutableArtifact) as exc:
        cat.replace_body("prompt_pack", pack.name, pack.digest, mutated)
    assert exc.value.code == "IMMUTABLE_ARTIFACT"


# --- FL-7 ---


def test_fl7_unknown_tool_invoke_loud() -> None:
    from supervisor.catalog.providers.catalog_tools import CatalogToolProvider
    from supervisor.catalog.store import CompositionCatalog
    from supervisor.seams.types import ToolInvokeContext

    cat = CompositionCatalog()
    ts = cat.offer(_sample_tool_set())
    provider = CatalogToolProvider(ts)
    result = provider.invoke(ToolInvokeContext(), "nope", {})
    assert result.ok is False
    assert result.code == "TOOL_NOT_FOUND"
    assert "nope" in (result.error or "")


# --- AR-1 ---


def test_ar1_journal_api_do_not_import_catalog_providers() -> None:
    base = _supervisor_src()
    for rel in ("journal", "api"):
        root = base / rel
        if not root.is_dir():
            continue
        for path in root.rglob("*.py"):
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module:
                    assert "catalog.providers" not in node.module, f"{path}: {node.module}"


# --- AR-2 ---


def test_ar2_session_pins_json_is_digest_strings_only() -> None:
    from supervisor.catalog.resolve import resolve_composition
    from supervisor.catalog.store import CompositionCatalog

    cat = CompositionCatalog()
    comp, _, _ = _offer_composition(cat)
    pins = resolve_composition(cat, composition_id=comp.name)
    data = pins.to_json()
    assert set(data.keys()) == {
        "composition_id",
        "composition_digest",
        "harness_version",
        "prompt_pack_id",
        "prompt_pack_digest",
        "tool_set_id",
        "tool_set_digest",
    }
    for v in data.values():
        assert isinstance(v, str)
    blob = str(data)
    assert "CatalogToolProvider" not in blob
    assert "CatalogPromptAssembler" not in blob


# --- AR-3 ---


def test_ar3_runtimes_do_not_construct_catalog() -> None:
    base = _supervisor_src() / "agent" / "runtimes"
    for path in base.rglob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                assert "catalog" not in node.module, f"{path}: {node.module}"
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert "catalog" not in alias.name


# --- AR-4 ---


def test_ar4_harness_yaml_tools_load_without_toolset() -> None:
    from pathlib import Path

    from supervisor.harness import load_manifest

    yaml_path = (
        Path(__file__).resolve().parents[4] / "harness" / "versions" / "coding-v1.yaml"
    )
    if not yaml_path.is_file():
        pytest.skip("coding-v1.yaml not present")
    manifest = load_manifest(yaml_path.read_text())
    assert manifest.tools is not None


# --- AR-5 / HP-6 helpers use session_env ---


@pytest.mark.asyncio
async def test_ar5_create_without_composition_keeps_seams_none(session_env) -> None:
    from unittest.mock import patch

    from supervisor.agent.factory import create_runtime

    captured: dict = {}

    def spy(name: str, **kwargs):  # type: ignore[no-untyped-def]
        captured["kwargs"] = kwargs
        return create_runtime(name, **kwargs)

    m = session_env["manager"]
    with patch("supervisor.session.manager.create_runtime", side_effect=spy):
        sid = await m.create(tenant_id="t1", harness_version="kg-v1", runtime_name="echo")
    assert captured["kwargs"].get("seams") is None
    await m.terminate(sid)


@pytest.mark.asyncio
async def test_hp6_create_with_composition_persists_pins_and_seams(session_env) -> None:
    from unittest.mock import patch

    from supervisor.agent.factory import create_runtime
    from supervisor.catalog.store import CompositionCatalog
    from supervisor.db.models import SessionRow

    cat = CompositionCatalog()
    comp, _, _ = _offer_composition(cat)
    session_env["manager"]._composition_catalog = cat

    captured: dict = {}

    def spy(name: str, **kwargs):  # type: ignore[no-untyped-def]
        captured["kwargs"] = kwargs
        return create_runtime(name, **kwargs)

    m = session_env["manager"]
    with patch("supervisor.session.manager.create_runtime", side_effect=spy):
        sid = await m.create(
            tenant_id="t1",
            harness_version=comp.harness_version,
            runtime_name="echo",
            composition_id=comp.name,
        )
    seams = captured["kwargs"].get("seams")
    assert seams is not None
    assert seams.prompt_assembler is not None
    assert seams.tool_provider is not None
    async with session_env["factory"]() as db:
        row = await db.get(SessionRow, sid)
        assert row is not None
        assert row.composition_pins is not None
        assert row.composition_pins["composition_digest"] == comp.digest
        assert "CatalogToolProvider" not in str(row.composition_pins)
    await m.terminate(sid)


@pytest.mark.asyncio
async def test_create_session_harness_version_mismatch_vs_composition_returns_4xx(
    session_env,
) -> None:
    from supervisor.catalog.errors import HarnessVersionMismatch
    from supervisor.catalog.store import CompositionCatalog

    cat = CompositionCatalog()
    comp, _, _ = _offer_composition(cat)
    session_env["manager"]._composition_catalog = cat
    m = session_env["manager"]
    with pytest.raises(HarnessVersionMismatch) as ei:
        await m.create(
            tenant_id="t1",
            harness_version="kg-v1",
            runtime_name="echo",
            composition_id=comp.name,
        )
    assert ei.value.code == "HARNESS_VERSION_MISMATCH"
    assert ei.value.composition == "coding-v1"


# --- SC-1 ---


def test_sc1_two_sessions_isolated_pins_and_assemble() -> None:
    from supervisor.catalog.binder import bind_runtime_seams
    from supervisor.catalog.resolve import resolve_composition
    from supervisor.catalog.store import CompositionCatalog
    from supervisor.seams.types import PromptBindings

    cat = CompositionCatalog()
    pack_a = cat.offer(_sample_prompt_pack(name="pack-a", persona="alpha-agent"))
    pack_b = cat.offer(_sample_prompt_pack(name="pack-b", persona="beta-agent"))
    tools = cat.offer(_sample_tool_set())
    from supervisor.catalog.types import AgentComposition, DigestRef

    ca = cat.offer(
        AgentComposition(
            name="comp-a",
            semver="1.0.0",
            digest="",
            protocol_version=1,
            status="offered",
            harness_version="coding-v1",
            prompt_pack=DigestRef(id=pack_a.name, digest=pack_a.digest),
            tool_set=DigestRef(id=tools.name, digest=tools.digest),
        )
    )
    cb = cat.offer(
        AgentComposition(
            name="comp-b",
            semver="1.0.0",
            digest="",
            protocol_version=1,
            status="offered",
            harness_version="coding-v1",
            prompt_pack=DigestRef(id=pack_b.name, digest=pack_b.digest),
            tool_set=DigestRef(id=tools.name, digest=tools.digest),
        )
    )
    pins_a = resolve_composition(cat, composition_id=ca.name)
    pins_b = resolve_composition(cat, composition_id=cb.name)
    assert pins_a.prompt_pack_digest != pins_b.prompt_pack_digest
    seams_a = bind_runtime_seams(pins_a, cat)
    seams_b = bind_runtime_seams(pins_b, cat)
    text_a = seams_a.prompt_assembler.assemble(PromptBindings()).text  # type: ignore[union-attr]
    text_b = seams_b.prompt_assembler.assemble(PromptBindings()).text  # type: ignore[union-attr]
    assert "alpha-agent" in text_a
    assert "beta-agent" in text_b
    assert text_a != text_b


# --- SC-2 ---


def test_sc2_deprecate_after_pin_resume_uses_pinned_digest() -> None:
    from supervisor.catalog.binder import bind_runtime_seams
    from supervisor.catalog.resolve import resolve_composition
    from supervisor.catalog.store import CompositionCatalog
    from supervisor.seams.types import PromptBindings

    cat = CompositionCatalog()
    comp, pack, _ = _offer_composition(cat)
    pins = resolve_composition(cat, composition_id=comp.name)
    frozen = pins.prompt_pack_digest
    cat.deprecate("prompt_pack", pack.name, pack.digest)
    # Resume path: bind from stored pins (do not re-resolve floating catalog).
    seams = bind_runtime_seams(pins, cat)
    text = seams.prompt_assembler.assemble(PromptBindings()).text  # type: ignore[union-attr]
    assert "coding agent" in text
    assert pins.prompt_pack_digest == frozen


# --- SC-3 ---


def test_sc3_n50_resolve_bind_distinct_seams_instances() -> None:
    from supervisor.catalog.binder import bind_runtime_seams
    from supervisor.catalog.resolve import resolve_composition
    from supervisor.catalog.store import CompositionCatalog

    cat = CompositionCatalog()
    comp, _, _ = _offer_composition(cat)
    seams_list = []
    for _ in range(50):
        pins = resolve_composition(cat, composition_id=comp.name)
        seams_list.append(bind_runtime_seams(pins, cat))
    assert len({id(s) for s in seams_list}) == 50
    assert len({id(s.tool_provider) for s in seams_list}) == 50


# --- SW-1 ---


def test_sw1_swap_toolset_digest_without_runtime_ctor_edit() -> None:
    from supervisor.catalog.binder import bind_runtime_seams
    from supervisor.catalog.resolve import resolve_composition
    from supervisor.catalog.store import CompositionCatalog
    from supervisor.catalog.types import AgentComposition, DigestRef

    cat = CompositionCatalog()
    pack = cat.offer(_sample_prompt_pack())
    ts1 = cat.offer(_sample_tool_set(name="tools-v1", tool_names=("echo",)))
    ts2 = cat.offer(_sample_tool_set(name="tools-v2", tool_names=("echo", "grep")))
    c1 = cat.offer(
        AgentComposition(
            name="c1",
            semver="1.0.0",
            digest="",
            protocol_version=1,
            status="offered",
            harness_version="coding-v1",
            prompt_pack=DigestRef(id=pack.name, digest=pack.digest),
            tool_set=DigestRef(id=ts1.name, digest=ts1.digest),
        )
    )
    c2 = cat.offer(
        AgentComposition(
            name="c2",
            semver="1.0.0",
            digest="",
            protocol_version=1,
            status="offered",
            harness_version="coding-v1",
            prompt_pack=DigestRef(id=pack.name, digest=pack.digest),
            tool_set=DigestRef(id=ts2.name, digest=ts2.digest),
        )
    )
    s1 = bind_runtime_seams(resolve_composition(cat, composition_id=c1.name), cat)
    s2 = bind_runtime_seams(resolve_composition(cat, composition_id=c2.name), cat)
    names1 = {t.name for t in s1.tool_provider.list_tools()}  # type: ignore[union-attr]
    names2 = {t.name for t in s2.tool_provider.list_tools()}  # type: ignore[union-attr]
    assert names1 == {"echo"}
    assert names2 == {"echo", "grep"}
    # Runtime factory body unchanged — catalog swap only.
    factory = (_supervisor_src() / "agent" / "factory.py").read_text()
    assert "CompositionCatalog" not in factory
    assert "CatalogToolProvider" not in factory
