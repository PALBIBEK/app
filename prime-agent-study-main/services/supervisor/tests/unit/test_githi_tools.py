"""WP-A4: githi file tools, path confinement, ToolProvider seam."""

from __future__ import annotations

from pathlib import Path

import pytest

from supervisor.agent.factory import create_runtime
from supervisor.agent.runtimes.githi.tools.paths import (
    PathEscapeError,
    resolve_in_workspace,
)
from supervisor.agent.runtimes.githi.tools.provider import GithiToolProvider
from supervisor.agent.runtimes.githi.tools.registry import (
    get_registry,
    reset_registry_for_tests,
)
from supervisor.seams.tool_provider import ToolProvider
from supervisor.seams.types import ToolInvokeContext


@pytest.fixture(autouse=True)
def _fresh_registry() -> None:
    reset_registry_for_tests()
    yield
    reset_registry_for_tests()


def test_builtin_registry_has_file_tools() -> None:
    reg = get_registry()
    assert set(reg.list_tool_names()) >= {"read", "write", "ls"}


def test_resolve_rejects_parent_escape(tmp_path: Path) -> None:
    root = tmp_path / "ws"
    root.mkdir()
    with pytest.raises(PathEscapeError):
        resolve_in_workspace(root, "../secret")


def test_resolve_rejects_absolute_outside(tmp_path: Path) -> None:
    root = tmp_path / "ws"
    root.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("x", encoding="utf-8")
    with pytest.raises(PathEscapeError):
        resolve_in_workspace(root, str(outside))


def test_resolve_allows_relative_inside(tmp_path: Path) -> None:
    root = tmp_path / "ws"
    root.mkdir()
    (root / "a.txt").write_text("ok", encoding="utf-8")
    got = resolve_in_workspace(root, "a.txt")
    assert got == (root / "a.txt").resolve()


def test_githi_tool_provider_is_tool_provider_protocol(tmp_path: Path) -> None:
    provider = GithiToolProvider("sess", working_directory=str(tmp_path))
    assert isinstance(provider, ToolProvider)
    names = {t.name for t in provider.list_tools()}
    assert {"read", "write", "ls"} <= names


def test_provider_write_read_ls_roundtrip(tmp_path: Path) -> None:
    provider = GithiToolProvider("sess", working_directory=str(tmp_path))
    ctx = ToolInvokeContext(trace_id="t1")

    w = provider.invoke(
        ctx,
        "write",
        {"file_path": "notes/hi.txt", "content": "hello\n"},
    )
    assert w.ok is True
    assert (tmp_path / "notes" / "hi.txt").read_text(encoding="utf-8") == "hello\n"

    r = provider.invoke(ctx, "read", {"file_path": "notes/hi.txt"})
    assert r.ok is True
    assert r.payload == "hello\n"

    listing = provider.invoke(ctx, "ls", {"path": "notes"})
    assert listing.ok is True
    assert "hi.txt" in str(listing.payload)


def test_provider_path_escape_is_tool_error(tmp_path: Path) -> None:
    provider = GithiToolProvider("sess", working_directory=str(tmp_path))
    ctx = ToolInvokeContext()
    result = provider.invoke(ctx, "read", {"file_path": "../etc/passwd"})
    assert result.ok is False
    assert result.code == "TOOL_ERROR"
    assert "escapes" in (result.error or "").lower()


@pytest.mark.asyncio
async def test_runtime_start_injects_tool_provider(tmp_path: Path) -> None:
    rt = create_runtime("githi")
    assert rt.seams is None or rt.seams.tool_provider is None
    await rt.start(None, str(tmp_path))
    assert rt.seams is not None
    assert rt.seams.tool_provider is not None
    assert isinstance(rt.seams.tool_provider, ToolProvider)
    names = {t.name for t in rt.seams.tool_provider.list_tools()}
    assert {"read", "write", "ls"} <= names
    await rt.stop()


def test_githi_tools_catalog_artifact_exists() -> None:
    from supervisor.catalog.bootstrap import default_catalog_dir
    from supervisor.catalog.digests import content_digest
    import json

    path = default_catalog_dir() / "tool_sets" / "githi-tools.json"
    assert path.is_file()
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["name"] == "githi-tools"
    assert set(raw["tools"]) == {"read", "write", "ls"}
    for name, digest in raw["tools"].items():
        body = {
            "name": raw["definitions"][name]["name"],
            "description": raw["definitions"][name]["description"],
            "parameters": raw["definitions"][name]["parameters"],
            "tool_class": raw["definitions"][name]["tool_class"],
        }
        assert digest == content_digest(body)
