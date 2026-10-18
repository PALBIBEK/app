"""Harness-binders — materialize catalog seams into session_dir/.yal/ (QUEUE step 3)."""

from __future__ import annotations

from pathlib import Path



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




def test_hb_c1c_coding_v1_manifest_uses_harness_runtime() -> None:
    import yaml

    root = Path(__file__).resolve().parents[4]
    coding = yaml.safe_load((root / "harness" / "versions" / "coding-v1.yaml").read_text())
    assert coding["runtime"] == "harness"




def test_hb_c2_harness_binders_contract_doc_exists() -> None:
    root = Path(__file__).resolve().parents[4]
    doc = root / "contracts" / "harness_binders.md"
    assert doc.is_file()
    text = doc.read_text()
    for needle in (".yal", "system_prompt.txt", "tools.json", "pins.json", "recovery"):
        assert needle in text, f"missing {needle!r}"


