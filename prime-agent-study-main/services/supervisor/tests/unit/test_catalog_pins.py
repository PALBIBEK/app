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




# --- HP-2 ---




# --- HP-3 ---




# --- HP-4 ---




# --- HP-5 ---




# --- HP-7 ---




# --- FL-1 ---




# --- FL-2 ---




# --- FL-3 ---




# --- FL-4 ---




# --- FL-5 ---




# --- FL-6 ---




# --- FL-7 ---




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






# --- SC-1 ---




# --- SC-2 ---




# --- SC-3 ---




# --- SW-1 ---


