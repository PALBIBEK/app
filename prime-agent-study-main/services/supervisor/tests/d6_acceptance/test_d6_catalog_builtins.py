"""D6 §5 A / T1–T2 — offered ToolSet builtins must not return TOOL_NOT_IMPLEMENTED."""

from __future__ import annotations

import pytest

from supervisor.catalog.providers.catalog_tools import CatalogToolProvider
from supervisor.seams.types import ToolInvokeContext


def _sample_args(name: str) -> dict:
    """Minimal args so handlers (when implemented) can run without KeyError."""
    if name == "echo":
        return {"text": "d6-echo"}
    if name == "bash":
        return {"command": "true"}
    if name == "grep":
        return {"pattern": "d6"}
    if name == "git_commit":
        return {"message": "d6"}
    if name == "read":
        return {"path": "README.md", "file_path": "README.md"}
    if name == "write":
        return {"path": "out.txt", "file_path": "out.txt", "content": "x"}
    if name == "ls":
        return {"path": "."}
    if name == "remember":
        return {"content": "d6-remember-fact"}
    return {}


@pytest.mark.parametrize("tool_set_name", ["coding-tools", "githi-tools"])
def test_a_offered_toolset_invokes_without_not_implemented(
    shipped_catalog, tool_set_name: str
) -> None:
    """§5 A / T1–T2: every name in offered coding-tools & githi-tools has a real handler."""
    arts = [
        a
        for a in shipped_catalog.list_artifacts("tool_set", status="offered")
        if a.name == tool_set_name
    ]
    assert arts, f"missing offered ToolSet {tool_set_name}"
    ts = arts[0]
    provider = CatalogToolProvider(ts)
    listed = {t.name for t in provider.list_tools()}
    assert listed == set(ts.tools.keys())

    failures: list[str] = []
    for name in sorted(ts.tools.keys()):
        result = provider.invoke(ToolInvokeContext(), name, _sample_args(name))
        if result.code == "TOOL_NOT_IMPLEMENTED":
            failures.append(name)
        # Unknown is wrong for a listed tool; deny/error ok if sandbox/IO missing.
        assert result.code != "TOOL_NOT_FOUND", name
    assert not failures, (
        f"{tool_set_name}: tools still TOOL_NOT_IMPLEMENTED (D6 handlers missing): "
        + ", ".join(failures)
    )


def test_t1_echo_ok_payload(shipped_catalog) -> None:
    """T1: Catalog echo invoke → ok + {\"text\":…}."""
    arts = [
        a
        for a in shipped_catalog.list_artifacts("tool_set", status="offered")
        if a.name == "coding-tools"
    ]
    assert arts
    provider = CatalogToolProvider(arts[0])
    result = provider.invoke(ToolInvokeContext(), "echo", {"text": "hello-d6"})
    assert result.ok is True
    assert result.code is None or result.code == "ok"
    assert result.payload == {"text": "hello-d6"}


def test_a_unknown_tool_not_found(shipped_catalog) -> None:
    """§5 A: unknown tool → TOOL_NOT_FOUND."""
    arts = [
        a
        for a in shipped_catalog.list_artifacts("tool_set", status="offered")
        if a.name == "coding-tools"
    ]
    provider = CatalogToolProvider(arts[0])
    result = provider.invoke(ToolInvokeContext(), "definitely_not_a_tool", {})
    assert result.ok is False
    assert result.code == "TOOL_NOT_FOUND"
