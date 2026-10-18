"""D7 §5 C / T8 — agent write path (remember tool or equivalent) into Tier 2."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from supervisor.catalog.bootstrap import bootstrap_composition_catalog, default_catalog_dir
from supervisor.catalog.tool_handlers import BUILTIN_HANDLERS
from tests.d7_acceptance.conftest import AI_REPO


def test_t8_remember_handler_registered() -> None:
    """T8 / §5 C: BuiltinHandlers must include remember (or durable write alias)."""
    write_names = {"remember", "memory_write", "write_memory", "durable_remember"}
    present = write_names & set(BUILTIN_HANDLERS)
    assert present, (
        "D7 T8: no remember/memory_write builtin handler — agent cannot write Tier 2 facts"
    )


def test_t8_remember_in_offered_toolset_or_catalog() -> None:
    """T8: an offered ToolSet must expose remember (or documented write tool)."""
    catalog = bootstrap_composition_catalog(default_catalog_dir(), fail_on_verify=False)
    tool_sets = catalog.list_artifacts("tool_set", status="offered")
    assert tool_sets, "expected offered ToolSets"
    names: set[str] = set()
    for ts in tool_sets:
        names.update(ts.tools.keys())
    write_names = {"remember", "memory_write", "write_memory", "durable_remember"}
    assert names & write_names, (
        "D7 T8: no offered ToolSet lists remember/memory_write — "
        f"tools seen sample={sorted(names)[:20]}"
    )


def test_t8_githi_recovery_class_alone_does_not_satisfy_write_path() -> None:
    """Trap: githi-v1.yaml may list remember recovery class without ToolSet/handler."""
    yaml_path = AI_REPO / "harness" / "versions" / "githi-v1.yaml"
    assert yaml_path.is_file()
    text = yaml_path.read_text(encoding="utf-8")
    # If YAML mentions remember, product still needs ToolSet+handler (covered above).
    # This assert documents the trap exists in-repo today OR write path is fully wired.
    tool_set_path = AI_REPO / "harness" / "tool_sets" / "githi-tools.json"
    data = json.loads(tool_set_path.read_text(encoding="utf-8"))
    yaml_has_remember = "remember:" in text
    toolset_has = "remember" in data.get("tools", {})
    handler_has = "remember" in BUILTIN_HANDLERS
    if yaml_has_remember and not (toolset_has and handler_has):
        pytest.fail(
            "D7 T8 trap: githi-v1.yaml lists remember recovery class but "
            "ToolSet/handler are missing — wire write path for acceptance"
        )
    assert toolset_has and handler_has, "D7 T8: remember ToolSet + handler required"
