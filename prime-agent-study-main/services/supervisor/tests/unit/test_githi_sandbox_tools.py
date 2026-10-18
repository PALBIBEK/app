"""WP-M5: githi sandbox TreeIO tools, path confinement, env sanitization."""

from __future__ import annotations

from pathlib import Path

import pytest

from supervisor.agent.factory import create_runtime
from supervisor.agent.runtimes.githi.tools.provider import GithiToolProvider
from supervisor.agent.runtimes.githi.tools.registry import reset_registry_for_tests
from supervisor.agent.runtimes.githi.tools.workspace_paths import (
    PathEscapeError,
    workspace_relpath,
)
from supervisor.sandbox.env import is_denied_env_key, sanitize_sandbox_env
from supervisor.sandbox.local_process import LocalProcessProvider
from supervisor.sandbox.workspace_tree import LocalFsTreeIO
from supervisor.seams.types import ToolInvokeContext


@pytest.fixture(autouse=True)
def _fresh_registry() -> None:
    reset_registry_for_tests()
    yield
    reset_registry_for_tests()


def test_workspace_relpath_rejects_escape() -> None:
    with pytest.raises(PathEscapeError):
        workspace_relpath("../etc/passwd")
    with pytest.raises(PathEscapeError):
        workspace_relpath("/etc/passwd")


def test_workspace_relpath_strips_workspace_prefix() -> None:
    assert workspace_relpath("/workspace/a/b.txt") == "a/b.txt"
    assert workspace_relpath("a/b.txt") == "a/b.txt"


def test_sanitize_denies_provider_secrets() -> None:
    assert is_denied_env_key("OPENAI_API_KEY")
    assert is_denied_env_key("OPENROUTER_API_KEY")
    assert is_denied_env_key("MODEL_GATEWAY_TOKEN")
    env = sanitize_sandbox_env(
        {"OPENAI_API_KEY": "sk-leak", "MY_SAFE_FLAG": "1"},
        base={"PATH": "/bin", "OPENAI_API_KEY": "sk-host"},
    )
    assert "OPENAI_API_KEY" not in env
    assert env.get("PATH") == "/bin"
    assert env.get("MY_SAFE_FLAG") == "1"


@pytest.mark.asyncio
async def test_provider_treeio_roundtrip_does_not_touch_outside(
    tmp_path: Path,
) -> None:
    root = tmp_path / "sbx"
    root.mkdir()
    outside = tmp_path / "secret.txt"
    outside.write_text("nope", encoding="utf-8")
    tree = LocalFsTreeIO(root)
    dirty = {"n": 0}

    provider = GithiToolProvider(
        "sess",
        working_directory=str(root),
        sandbox_enabled=True,
        tree=tree,
        on_mutate=lambda: dirty.__setitem__("n", dirty["n"] + 1),
    )
    ctx = ToolInvokeContext(trace_id="t1")
    w = provider.invoke(
        ctx, "write", {"file_path": "notes/hi.txt", "content": "hello\n"}
    )
    assert w.ok is True
    assert dirty["n"] == 1
    assert (root / "notes" / "hi.txt").read_text(encoding="utf-8") == "hello\n"
    assert outside.read_text(encoding="utf-8") == "nope"

    r = provider.invoke(ctx, "read", {"file_path": "notes/hi.txt"})
    assert r.ok is True
    assert r.payload == "hello\n"

    listing = provider.invoke(ctx, "ls", {"path": "notes"})
    assert listing.ok is True
    assert "hi.txt" in str(listing.payload)

    escaped = provider.invoke(ctx, "read", {"file_path": "../secret.txt"})
    assert escaped.ok is False
    assert escaped.code == "TOOL_ERROR"


def test_provider_strips_model_scope_id(tmp_path: Path) -> None:
    tree = LocalFsTreeIO(tmp_path)
    provider = GithiToolProvider(
        "sess", working_directory=str(tmp_path), sandbox_enabled=True, tree=tree
    )
    result = provider.invoke(
        ToolInvokeContext(),
        "write",
        {
            "file_path": "x.txt",
            "content": "y",
            "scope_id": "attacker-scope",
        },
    )
    assert result.ok is True


def test_sandbox_enabled_without_tree_fails() -> None:
    with pytest.raises(ValueError):
        GithiToolProvider("sess", sandbox_enabled=True, tree=None)


@pytest.mark.asyncio
async def test_runtime_binds_sandbox_tree(tmp_path: Path) -> None:
    provider = LocalProcessProvider(base_dir=tmp_path / "sandboxes")
    sandbox = await provider.create(image="sha256:" + "b" * 64)
    rt = create_runtime("githi")
    await rt.start(sandbox, str(tmp_path / "session"))
    assert rt.sandbox is sandbox
    assert rt.seams is not None
    assert isinstance(rt.seams.tool_provider, GithiToolProvider)
    assert rt.seams.tool_provider.sandbox_enabled is True
    assert rt.seams.tool_provider.tree is not None
    await rt.stop()
