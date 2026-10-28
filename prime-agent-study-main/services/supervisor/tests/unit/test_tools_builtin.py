"""Built-in tools behave the same on a host directory and on a sandbox file tree."""

from __future__ import annotations

import asyncio
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from supervisor.sandbox.providers.local_process import LocalProcessSandbox
from supervisor.sandbox.workspace_tree import LocalFsTreeIO
from supervisor.tools import (
    FileMissing,
    HostWorkspace,
    InvalidArguments,
    PathEscape,
    SandboxWorkspace,
    ToolContext,
    TreeWorkspace,
    UnsupportedCapability,
    Workspace,
    default_registry,
)


@pytest.fixture(params=["host", "tree", "sandbox"])
def workspace(request: pytest.FixtureRequest, tmp_path: Path) -> Workspace:
    root = tmp_path / "ws"
    root.mkdir()
    (root / "src").mkdir()
    (root / "src" / "app.py").write_text("import os\nprint('hello')\n")
    (root / "src" / "util.py").write_text("def helper():\n    return 'HELLO'\n")
    (root / "README").write_text("line 1\nline 2\nline 3\n")
    (root / ".env").write_text("SECRET=1\n")
    return {
        "host": lambda: HostWorkspace(root),
        "tree": lambda: TreeWorkspace(LocalFsTreeIO(root)),
        "sandbox": lambda: SandboxWorkspace(LocalProcessSandbox(root, "sb-test")),
    }[request.param]()


@pytest.fixture
def mutations() -> list[str]:
    return []


@pytest.fixture
def call(workspace: Workspace, mutations: list[str]) -> Callable[..., Any]:
    ctx = ToolContext(
        session_id="s1",
        bound_workspace=workspace,
        timeout_s=10,
        on_mutate=lambda: mutations.append("mutated"),
    )

    async def run(tool: str, **args: Any) -> Any:
        return await default_registry()[tool].run(args, ctx)

    return run


# --- read --------------------------------------------------------------------


async def test_read_pages_with_a_resume_offset(call: Callable[..., Any]) -> None:
    assert await call("read", file_path="README") == "line 1\nline 2\nline 3\n"
    assert await call("read", file_path="README", offset=2, limit=1) == (
        "line 2\n\n[1 more lines in file. Use offset=3 to continue.]"
    )
    assert await call("read", path="/workspace/README", offset=3) == "line 3\n"
    with pytest.raises(InvalidArguments, match=r"Offset 9 is beyond end of file \(3 lines total\)"):
        await call("read", file_path="README", offset=9)


async def test_read_caps_a_long_file_and_says_where_to_go_on(call: Callable[..., Any]) -> None:
    await call("write", file_path="big.txt", content="".join(f"row {i}\n" for i in range(1, 2501)))
    page = await call("read", file_path="big.txt")
    assert page.splitlines()[1999] == "row 2000"
    assert page.endswith("[Showing lines 1-2000 of 2500. Use offset=2001 to continue.]")
    assert (await call("read", file_path="big.txt", offset=2001)).splitlines()[0] == "row 2001"


async def test_read_cuts_by_bytes_and_points_past_a_line_longer_than_a_page(
    call: Callable[..., Any],
) -> None:
    await call("write", path="wide.txt", content="".join(f"{i:04d}{'x' * 96}\n" for i in range(1000)))
    page = await call("read", path="wide.txt")
    assert page.endswith("[Showing lines 1-506 of 1000 (50.0KB limit). Use offset=507 to continue.]")
    await call("write", path="blob.min.js", content="y" * 60_000 + "\nnext\n")
    assert await call("read", path="blob.min.js") == (
        "[Line 1 is 58.6KB, exceeds 50.0KB limit. Use bash: sed -n '1p' blob.min.js | head -c 51200]"
    )
    assert await call("read", path="blob.min.js", offset=2) == "next\n"


async def test_read_reports_binary_and_images_instead_of_garbage(
    call: Callable[..., Any], workspace: Workspace
) -> None:
    await workspace.write_bytes("pic.png", b"\x89PNG\r\n\x1a\n" + b"\x00" * 20)
    await workspace.write_bytes("blob.bin", b"ab\x00cd")
    await workspace.write_bytes("empty.txt", b"")
    assert await call("read", file_path="pic.png") == "pic.png is a png image (28 bytes); it cannot be shown as text."
    assert await call("read", file_path="blob.bin") == "blob.bin is a binary file (5 bytes); it cannot be shown as text."
    assert await call("read", file_path="empty.txt") == "empty.txt is empty."


@pytest.mark.parametrize("path", ["../outside", "/etc/passwd", "src/../../x"])
async def test_paths_cannot_leave_the_workspace(call: Callable[..., Any], path: str) -> None:
    with pytest.raises(PathEscape):
        await call("read", file_path=path)


@pytest.mark.parametrize("tool", ["read", "write"])
async def test_a_symlink_out_of_the_workspace_is_a_path_escape(
    call: Callable[..., Any], tmp_path: Path, tool: str
) -> None:
    """Not a tool crash: the model is told the path is outside, as for ``..`` (F22)."""
    secret = tmp_path / "outside.txt"
    secret.write_text("host secret\n")
    (tmp_path / "ws" / "link").symlink_to(secret)
    args = {"content": "x"} if tool == "write" else {}
    with pytest.raises(PathEscape):
        await call(tool, file_path="link", **args)
    assert secret.read_text() == "host secret\n"


async def test_read_missing_file(call: Callable[..., Any]) -> None:
    with pytest.raises(FileMissing, match="nope.txt"):
        await call("read", file_path="nope.txt")


async def test_read_rejects_bad_arguments_with_a_readable_reason(call: Callable[..., Any]) -> None:
    with pytest.raises(InvalidArguments, match=r'Validation failed for tool "read":\n  - path: Field required'):
        await call("read")
    with pytest.raises(InvalidArguments, match=r'Received arguments:\n\{\n  "path": "README",\n  "offset": 0'):
        await call("read", path="README", offset=0)
    assert await call("read", path="README", offset=None, limit=None) == "line 1\nline 2\nline 3\n"  # null: not sent
    with pytest.raises(
        InvalidArguments, match="offset: Input should be greater than or equal to 1"
    ):
        await call("read", file_path="README", offset=0)


# --- write / edit ---------------------------------------------------------------


async def test_write_creates_parents_and_reports_the_change(
    call: Callable[..., Any], mutations: list[str]
) -> None:
    assert await call("write", file_path="new/dir/a.txt", content="héllo") == {
        "path": "new/dir/a.txt",
        "bytes": 6,
    }
    assert await call("read", file_path="new/dir/a.txt") == "héllo"
    assert mutations == ["mutated"]


async def test_write_accepts_the_coding_tools_argument_name(call: Callable[..., Any]) -> None:
    await call("write", path="b.txt", content="x")
    assert await call("read", path="b.txt") == "x"


async def test_edit_requires_a_unique_match(call: Callable[..., Any], workspace: Workspace) -> None:
    await call("write", file_path="f.txt", content="a-b-a")
    with pytest.raises(InvalidArguments, match="Found 2 occurrences of the text in f.txt"):
        await call("edit", file_path="f.txt", old_text="a", new_text="z")
    with pytest.raises(InvalidArguments, match="Could not find the exact text in f.txt"):
        await call("edit", path="f.txt", edits=[{"oldText": "q", "newText": "z"}])
    done = await call("edit", file_path="f.txt", old_text="-b-", new_text="+")
    assert (done["path"], done["replacements"]) == ("f.txt", 1)
    assert "-a-b-a" in done["diff"] and "+a+a" in done["diff"]
    done = await call("edit", file_path="f.txt", old_text="a", new_text="o", replace_all=True)
    assert done["replacements"] == 2
    assert await workspace.read_bytes("f.txt") == b"o+o"


async def test_edit_keeps_line_endings_bom_and_matches_curly_quotes(
    call: Callable[..., Any], workspace: Workspace
) -> None:
    await workspace.write_bytes("w.txt", "\ufeffsay \u201chi\u201d\r\nit\u2019s here\r\n".encode())
    await call("edit", file_path="w.txt", old_text="it's here\n", new_text="it's there\n")
    assert await workspace.read_bytes("w.txt") == "\ufeffsay \u201chi\u201d\r\nit's there\r\n".encode()


async def test_several_edits_apply_together_or_not_at_all(call: Callable[..., Any], workspace: Workspace) -> None:
    await call("write", file_path="g.py", content="x = 1\ny = 2\nz = 3\n")
    with pytest.raises(InvalidArguments, match=r"Could not find edits\[1\] in g.py"):
        await call("edit", file_path="g.py", edits=[{"old_text": "x = 1", "new_text": "x = 10"},
                                                    {"old_text": "nope", "new_text": "?"}])
    assert await workspace.read_bytes("g.py") == b"x = 1\ny = 2\nz = 3\n"  # untouched
    with pytest.raises(InvalidArguments, match="overlap"):
        await call("edit", file_path="g.py", edits=[{"old_text": "x = 1\ny", "new_text": "a"},
                                                    {"old_text": "y = 2", "new_text": "b"}])
    with pytest.raises(InvalidArguments, match="No changes made to g.py"):
        await call("edit", file_path="g.py", old_text="z = 3", new_text="z = 3")
    done = await call("edit", file_path="g.py", edits=[{"old_text": "x = 1", "new_text": "x = 10"},
                                                       {"old_text": "z = 3", "new_text": "z = 30"}])
    assert done["replacements"] == 2
    assert await workspace.read_bytes("g.py") == b"x = 10\ny = 2\nz = 30\n"


# --- ls / grep / find --------------------------------------------------------------


async def test_ls_lists_children_as_pi_does(call: Callable[..., Any], workspace: Workspace) -> None:
    assert await call("ls") == ".env\nREADME\nsrc/"  # dotfiles included, directories marked
    assert await call("ls", show_hidden=False) == ".env\nREADME\nsrc/"  # an older tool set's argument
    assert await call("ls", path="src") == "app.py\nutil.py"
    assert await call("ls", limit=2) == ".env\nREADME\n\n[2 entries limit reached. Use limit=4 for more]"
    await workspace.write_bytes("src/Zeta.py", b"")
    assert (await call("ls", path="src")).splitlines() == ["app.py", "util.py", "Zeta.py"]  # case ignored
    with pytest.raises(FileMissing, match="missing"):
        await call("ls", path="missing")
    with pytest.raises(InvalidArguments, match="Not a directory: README"):
        await call("ls", path="README")


async def test_grep_returns_pis_lines_and_notes(call: Callable[..., Any]) -> None:
    assert await call("grep", pattern="hello") == "src/app.py:2: print('hello')"
    assert await call("grep", pattern="hello", ignoreCase=True, glob="*.py") == (
        "src/app.py:2: print('hello')\nsrc/util.py:2:     return 'HELLO'"
    )
    assert await call("grep", pattern="hello", case_insensitive=True, path="src") == (
        "app.py:2: print('hello')\nutil.py:2:     return 'HELLO'"  # relative to the directory searched
    )
    assert await call("grep", pattern="line", path="README", limit=2) == (
        "README:1: line 1\nREADME:2: line 2\n\n[2 matches limit reached. Use limit=4 for more, or refine pattern]"
    )
    assert await call("grep", pattern="print", context=1) == "src/app.py-1- import os\nsrc/app.py:2: print('hello')"
    assert await call("grep", pattern="SECRET") == ".env:1: SECRET=1"  # dotfiles are searched
    assert await call("grep", pattern="print(", literal=True) == "src/app.py:2: print('hello')"
    assert await call("grep", pattern="nothing here") == "No matches found"
    with pytest.raises(InvalidArguments, match="regex parse error"):
        await call("grep", pattern="(")


async def test_grep_and_find_respect_gitignore(call: Callable[..., Any], workspace: Workspace) -> None:
    await workspace.write_bytes(".gitignore", b"node_modules/\n*.log\n!keep.log\n")
    await workspace.write_bytes("node_modules/pkg/index.js", b"hello\n")
    await workspace.write_bytes("debug.log", b"hello\n")
    await workspace.write_bytes("keep.log", b"hello\n")
    await workspace.write_bytes("src/.gitignore", b"/gen/\n")
    await workspace.write_bytes("src/gen/out.py", b"hello\n")
    await workspace.write_bytes(".githi/output/bash-1.log", b"hello\n")
    assert await call("grep", pattern="hello") == "keep.log:1: hello\nsrc/app.py:2: print('hello')"
    assert await call("grep", pattern="hello", path="node_modules") == "pkg/index.js:1: hello"  # named: searched
    assert await call("find", pattern="*.log") == "keep.log"
    assert await call("find", pattern="*.py") == "src/app.py\nsrc/util.py"
    assert await call("find", pattern="src/*.py", path="") == "src/app.py\nsrc/util.py"
    assert await call("find", pattern="*", path="src") == ".gitignore\napp.py\nutil.py"


async def test_find_returns_pis_paths_and_notes(call: Callable[..., Any]) -> None:
    assert await call("find", pattern="*.py") == "src/app.py\nsrc/util.py"
    assert await call("find", pattern="src") == "src/"  # directories too, marked
    assert await call("find", pattern="**/*.py", path="src") == "app.py\nutil.py"
    assert await call("find", pattern="*", limit=2) == (
        ".env\nREADME\n\n[2 results limit reached. Use limit=4 for more, or refine pattern]"
    )
    assert await call("find", pattern="*.rs") == "No files found matching pattern"
    with pytest.raises(FileMissing):
        await call("find", pattern="*", path="missing")
    with pytest.raises(InvalidArguments, match="Not a directory: README"):
        await call("find", pattern="*", path="README")


# --- echo -----------------------------------------------------------------------


async def test_echo_needs_no_workspace() -> None:
    assert await default_registry()["echo"].run({"text": "hi"}, ToolContext()) == {"text": "hi"}


# --- bash / git_commit (host only) -------------------------------------------------


@pytest.fixture
def host(tmp_path: Path) -> ToolContext:
    return ToolContext(session_id="s1", bound_workspace=HostWorkspace(tmp_path), timeout_s=10)


async def _run(tool: str, ctx: ToolContext, **args: Any) -> Any:
    return await default_registry()[tool].run(args, ctx)


async def test_bash_runs_in_the_workspace(host: ToolContext, tmp_path: Path) -> None:
    out = await _run("bash", host, command="pwd; echo err >&2; echo out; exit 3")
    # As pi's bash: one stream, in the order written.
    combined = f"{tmp_path.resolve()}\nerr\nout\n"
    assert out == {"exit_code": 3, "output": combined, "stdout": combined, "stderr": ""}


async def test_bash_does_not_leak_the_supervisor_environment(
    host: ToolContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GATEWAY_TOKEN", "super-secret")
    out = await _run("bash", host, command="env")
    assert "super-secret" not in out["stdout"]


async def test_bash_kills_a_command_at_its_timeout(host: ToolContext) -> None:
    out = await _run("bash", host, command="echo started; sleep 30", timeout=1)
    assert out["timed_out"] is True and out["stdout"] == "started\n"
    capped = await _run("bash", host, command="true", timeout=999)
    assert capped["timeout_capped_to"] == 8.0  # the call's 10s budget minus the kill grace


async def test_bash_needs_a_workspace_that_can_run_commands(tmp_path: Path) -> None:
    ctx = ToolContext(bound_workspace=TreeWorkspace(LocalFsTreeIO(tmp_path)))
    with pytest.raises(UnsupportedCapability):
        await _run("bash", ctx, command="ls")


async def test_git_commit(host: ToolContext, tmp_path: Path) -> None:
    assert await _run("git_commit", host, message="first") == {
        "committed": False,
        "reason": "the workspace is not a git repository",
    }
    await asyncio.to_thread(_git, tmp_path, "init", "-q")
    (tmp_path / "a.txt").write_text("a")
    out = await _run("git_commit", host, message="it's done")
    assert out["committed"] is True, out
    assert (
        await asyncio.to_thread(_git, tmp_path, "log", "--format=%s|%an") == "it's done|YAL agent"
    )


def _git(repo: Path, *args: str) -> str:
    done = subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    )
    return done.stdout.strip()


async def test_bash_runs_inside_the_sandbox(tmp_path: Path) -> None:
    sandbox = LocalProcessSandbox(tmp_path, "sb-bash")
    ctx = ToolContext(bound_workspace=SandboxWorkspace(sandbox), timeout_s=10)
    out = await _run("bash", ctx, command="echo hi > made.txt; cat made.txt; exit 2")
    assert (out["exit_code"], out["stdout"]) == (2, "hi\n")
    assert await sandbox.read_file("/workspace/made.txt") == b"hi\n"


async def test_bash_keeps_the_whole_of_an_output_it_cuts(host: ToolContext, tmp_path: Path) -> None:
    out = await _run("bash", host, command="seq 1 20000")
    assert out["output"].splitlines()[0] == "18001" and out["output"].endswith("20000")  # the tail
    assert out["truncation"] == {
        "by": "lines", "start": 18001, "end": 20000, "total": 20000, "shown_bytes": len(out["output"]),
        "partial": False,
    }
    spill = out["full_output"]
    assert spill.startswith(".githi/output/")
    kept = (tmp_path / spill).read_text()
    assert kept.splitlines()[0] == "1" and kept.splitlines()[-1] == "20000"
