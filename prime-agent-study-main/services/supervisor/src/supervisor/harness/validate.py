"""Harness tree validation — recovery tool names vs composition ToolSet."""

from __future__ import annotations

import json
from pathlib import Path

from supervisor.harness.storage.converters import load_manifest


def verify_recovery_tools_in_compositions(
    catalog_dir: Path,
    *,
    offered_names: set[str] | None = None,
) -> list[str]:
    """YAML ``tools:`` names must be a subset of linked composition ToolSet tools.

    When ``offered_names`` is set, only those compositions are checked (soft-skipped /
    draft fixtures must not brick bootstrap).
    """
    problems: list[str] = []
    versions_dir = catalog_dir / "versions"
    compositions_dir = catalog_dir / "compositions"
    tool_sets_dir = catalog_dir / "tool_sets"

    if not compositions_dir.is_dir():
        return [f"missing compositions dir: {compositions_dir}"]

    tool_sets: dict[str, set[str]] = {}
    for path in tool_sets_dir.glob("*.json"):
        raw = json.loads(path.read_text(encoding="utf-8"))
        names = set((raw.get("definitions") or {}).keys())
        tool_sets[raw["name"]] = names

    for comp_path in sorted(compositions_dir.glob("*.json")):
        comp = json.loads(comp_path.read_text(encoding="utf-8"))
        name = comp.get("name", comp_path.stem)
        if offered_names is not None and name not in offered_names:
            continue
        if comp.get("status", "offered") != "offered":
            continue
        harness_version = comp.get("harness_version", "")
        tool_ref = comp.get("tool_set") or {}
        tool_id = tool_ref.get("id", "")
        yaml_path = versions_dir / f"{harness_version}.yaml"
        if not yaml_path.is_file():
            problems.append(f"{comp_path.name}: missing harness manifest {yaml_path.name}")
            continue
        manifest = load_manifest(yaml_path.read_text(encoding="utf-8"))
        recovery_names = set(manifest.tools.keys())
        catalog_names = tool_sets.get(tool_id, set())
        if not catalog_names:
            problems.append(f"{comp_path.name}: unknown tool_set id {tool_id!r}")
            continue
        extra = sorted(recovery_names - catalog_names)
        if extra:
            problems.append(
                f"{comp_path.name}: harness {harness_version} recovery tools "
                f"not in ToolSet {tool_id}: {', '.join(extra)}"
            )

    return problems
