"""Harness-binders — materialize catalog seams into session_dir/.yal/ (QUEUE step 3)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest


def _sample_pack_and_tools():
    from supervisor.catalog.digests import content_digest
    from supervisor.catalog.types import (
        PromptPack,
        PromptSection,
        ToolDefinition,
        ToolSet,
    )

    pack = PromptPack(
        name="coding-prompt",
        semver="1.0.0",
        digest="",
        protocol_version=1,
        status="offered",
        persona="coder",
        sections=(
            PromptSection(id="intro", order=1, text="You are a coding agent.", complete=True),
        ),
        vars=(),
    )
    body = {
        "name": "echo",
        "description": "echo tool",
        "parameters": {"type": "object", "properties": {"text": {"type": "string"}}},
    }
    d = content_digest(body)
    tools = ToolSet(
        name="coding-tools",
        semver="1.0.0",
        digest="",
        protocol_version=1,
        status="offered",
        tools={"echo": d},
        definitions={
            "echo": ToolDefinition(
                name="echo",
                description=body["description"],
                parameters=body["parameters"],
                digest=d,
            )
        },
    )
    return pack, tools


def test_hb_u1_materialize_writes_yal_files(tmp_path: Path) -> None:
    from supervisor.catalog.providers.catalog_prompt import CatalogPromptAssembler
    from supervisor.catalog.providers.catalog_tools import CatalogToolProvider
    from supervisor.catalog.types import SessionPins
    from supervisor.harness import materialize_harness_binds
    from supervisor.seams.factory import build_runtime_seams

    pack, tools = _sample_pack_and_tools()
    seams = build_runtime_seams(
        tool_provider=CatalogToolProvider(tools),
        prompt_assembler=CatalogPromptAssembler(pack),
    )
    pins = SessionPins(
        composition_id="coding-v1",
        composition_digest="sha256:" + "a" * 64,
        harness_version="coding-v1",
        prompt_pack_id=pack.name,
        prompt_pack_digest="sha256:" + "b" * 64,
        tool_set_id=tools.name,
        tool_set_digest="sha256:" + "c" * 64,
    )
    yal = materialize_harness_binds(tmp_path, pins, seams)
    assert yal == tmp_path / ".yal"
    assert (yal / "system_prompt.txt").is_file()
    assert "coding agent" in (yal / "system_prompt.txt").read_text()
    tools_payload = json.loads((yal / "tools.json").read_text())
    assert tools_payload[0]["name"] == "echo"
    assert "description" in tools_payload[0]
    assert "parameters" in tools_payload[0]
    pins_payload = json.loads((yal / "pins.json").read_text())
    assert pins_payload["composition_id"] == "coding-v1"
    assert "CatalogToolProvider" not in (yal / "pins.json").read_text()


def test_hb_u2_null_tools_writes_empty_list(tmp_path: Path) -> None:
    from supervisor.catalog.types import SessionPins
    from supervisor.harness import materialize_harness_binds
    from supervisor.seams.factory import build_runtime_seams

    seams = build_runtime_seams()  # NullToolProvider + StaticPromptAssembler
    pins = SessionPins(
        composition_id="x",
        composition_digest="sha256:" + "a" * 64,
        harness_version="kg-v1",
        prompt_pack_id="p",
        prompt_pack_digest="sha256:" + "b" * 64,
        tool_set_id="t",
        tool_set_digest="sha256:" + "c" * 64,
    )
    yal = materialize_harness_binds(tmp_path, pins, seams)
    assert json.loads((yal / "tools.json").read_text()) == []
    assert (yal / "system_prompt.txt").read_text()


def test_hb_c1_yaml_tools_are_recovery_classes_not_toolset() -> None:
    import yaml

    root = Path(__file__).resolve().parents[4]  # agent-platform
    coding = yaml.safe_load((root / "harness" / "versions" / "coding-v1.yaml").read_text())
    tools = coding["tools"]
    assert isinstance(tools, dict)
    for name, spec in tools.items():
        assert "class" in spec, f"{name} missing recovery class"
        assert "digest" not in spec
        assert "parameters" not in spec


def test_hb_c1b_recovery_names_subset_of_composition_toolset() -> None:
    from supervisor.catalog.bootstrap import default_catalog_dir
    from supervisor.harness.validate import verify_recovery_tools_in_compositions

    catalog_dir = default_catalog_dir()
    problems = verify_recovery_tools_in_compositions(catalog_dir)
    assert problems == [], problems


def test_hb_c1c_coding_v1_manifest_uses_harness_runtime() -> None:
    import yaml

    root = Path(__file__).resolve().parents[4]
    coding = yaml.safe_load((root / "harness" / "versions" / "coding-v1.yaml").read_text())
    assert coding["runtime"] == "harness"


def test_hb_p0_extensions_read_shipped_catalog_binds(tmp_path: Path) -> None:
    import json
    import shutil
    import subprocess

    from supervisor.catalog.bootstrap import bootstrap_composition_catalog, default_catalog_dir

    catalog_dir = default_catalog_dir()
    catalog = bootstrap_composition_catalog(catalog_dir)
    comp = catalog.get(
        "composition",
        "coding-v1",
        next(
            art.digest
            for (kind, name, _), art in catalog._by_key.items()  # noqa: SLF001
            if kind == "composition" and name == "coding-v1" and art.status == "offered"
        ),
        require_offered=True,
    )
    from supervisor.catalog.binder import bind_runtime_seams
    from supervisor.catalog.resolve import resolve_composition
    from supervisor.harness import materialize_harness_binds
    from supervisor.seams.factory import build_runtime_seams

    pins = resolve_composition(catalog, composition_id=comp.name, composition_digest=comp.digest)
    seams = bind_runtime_seams(pins, catalog)
    workspace = tmp_path / "ws"
    materialize_harness_binds(workspace, pins, seams, session_id="p0", tenant_id="t1")

    harness_root = catalog_dir
    node = shutil.which("node")
    npm = shutil.which("npm")
    if node is None or npm is None:
        pytest.skip("node/npm not installed")

    build = subprocess.run(
        [npm, "run", "build"],
        cwd=harness_root,
        capture_output=True,
        text=True,
        check=False,
    )
    assert build.returncode == 0, build.stderr or build.stdout

    check = subprocess.run(
        [node, "scripts/check-binds.mjs", str(workspace)],
        cwd=harness_root,
        capture_output=True,
        text=True,
        check=False,
    )
    assert check.returncode == 0, check.stderr or check.stdout
    payload = json.loads(check.stdout.strip().splitlines()[-1])
    assert payload["ok"]
    assert payload["tool_count"] >= 1
    assert "echo" in payload["tool_names"]


def test_hb_c2_harness_binders_contract_doc_exists() -> None:
    root = Path(__file__).resolve().parents[4]
    doc = root / "contracts" / "harness_binders.md"
    assert doc.is_file()
    text = doc.read_text()
    for needle in (".yal", "system_prompt.txt", "tools.json", "pins.json", "recovery"):
        assert needle in text, f"missing {needle!r}"


@pytest.mark.asyncio
async def test_hb_i1_session_create_materializes_yal(session_env, tmp_path, monkeypatch) -> None:
    from unittest.mock import patch

    from supervisor.agent.factory import create_runtime
    from supervisor.catalog.store import CompositionCatalog

    # Reuse catalog helpers from catalog pins tests via inline offer
    from tests.unit.test_catalog_pins import _offer_composition

    monkeypatch.setenv("YAL_SESSION_WORKSPACES", str(tmp_path / "ws"))
    cat = CompositionCatalog()
    comp, _, _ = _offer_composition(cat)
    session_env["manager"]._composition_catalog = cat

    m = session_env["manager"]
    with patch("supervisor.session.manager.create_runtime", side_effect=create_runtime):
        sid = await m.create(
            tenant_id="t1",
            harness_version="kg-v1",
            runtime_name="echo",
            composition_id=comp.name,
        )
    yal = tmp_path / "ws" / sid / ".yal"
    assert (yal / "system_prompt.txt").is_file()
    assert (yal / "tools.json").is_file()
    assert (yal / "pins.json").is_file()
    await m.terminate(sid)
