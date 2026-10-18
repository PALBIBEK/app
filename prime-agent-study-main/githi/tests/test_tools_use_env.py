"""Tools must route filesystem access through `ExecutionEnv`.

`harness/env.py` exists and is tested, but nothing uses it: every tool calls
`pathlib` directly, so the abstraction buys exactly nothing. That is the point
of the port -- an `ExecutionEnv` is the seam that lets the *same* tools run
against a container or an SSH host, and githi's sandbox cannot become a
container while `read` reaches for `Path.read_bytes` itself.

The test strategy is deliberate: a **recording env** that satisfies the
protocol and counts calls. Asserting on a real temp directory would pass
whether or not the tool went through the env, because the outcome is
identical either way -- and that is precisely the bug. Only a substituted
backend proves the indirection is real.

Sandbox confinement stays where it is. The env decides *how* to reach a path;
`sandbox.paths` decides *whether* the path is allowed. Collapsing the two
would mean a container backend silently dropped the confinement checks.
"""

from __future__ import annotations

import pytest

from githi.harness.env import ExecOutput, FileError, FileErrorCode, LocalExecutionEnv, ok
from githi.sandbox.policy import SandboxPolicy
from githi.tools.base import ToolContext
from githi.tools.edit import edit_tool
from githi.tools.grep import grep_tool
from githi.tools.ls import ls_tool
from githi.tools.read import read_tool
from githi.tools.write import write_tool


class RecordingEnv(LocalExecutionEnv):
    """A real env that also counts what was asked of it."""

    def __init__(self, cwd):
        super().__init__(cwd)
        self.reads: list[str] = []
        self.writes: list[str] = []
        self.lists: list[str] = []

    def read_binary_file(self, path):
        self.reads.append(str(path))
        return super().read_binary_file(path)

    def read_text_file(self, path):
        self.reads.append(str(path))
        return super().read_text_file(path)

    def write_file(self, path, content):
        self.writes.append(str(path))
        return super().write_file(path, content)

    def list_dir(self, path):
        self.lists.append(str(path))
        return super().list_dir(path)


@pytest.fixture
def env(tmp_path):
    return RecordingEnv(tmp_path)


@pytest.fixture
def ctx(tmp_path, env):
    (tmp_path / "notes.txt").write_text("alpha\nbeta\ngamma\n", encoding="utf-8")
    (tmp_path / "pkg").mkdir()
    return ToolContext(policy=SandboxPolicy(root=tmp_path), env=env)


class TestToolsGoThroughTheEnv:
    def test_read_uses_the_env(self, ctx, env):
        read_tool().run({"path": "notes.txt"}, ctx)
        assert env.reads, "read bypassed the ExecutionEnv"

    def test_write_uses_the_env(self, ctx, env):
        write_tool().run({"path": "out.txt", "content": "data"}, ctx)
        assert env.writes, "write bypassed the ExecutionEnv"

    def test_edit_uses_the_env(self, ctx, env):
        edit_tool().run({"path": "notes.txt", "old": "beta", "new": "BETA"}, ctx)
        assert env.reads and env.writes, "edit bypassed the ExecutionEnv"

    def test_ls_uses_the_env(self, ctx, env):
        ls_tool().run({"path": "."}, ctx)
        assert env.lists, "ls bypassed the ExecutionEnv"

    def test_grep_uses_the_env(self, ctx, env):
        grep_tool().run({"pattern": "beta"}, ctx)
        assert env.reads, "grep bypassed the ExecutionEnv"


class TestBehaviourIsUnchanged:
    """The indirection must not alter any observable result."""

    def test_read_still_returns_content(self, ctx):
        assert "beta" in read_tool().run({"path": "notes.txt"}, ctx).text

    def test_write_still_writes(self, ctx):
        write_tool().run({"path": "out.txt", "content": "data"}, ctx)
        assert (ctx.policy.root / "out.txt").read_text() == "data"

    def test_edit_still_preserves_crlf(self, ctx):
        target = ctx.policy.root / "win.txt"
        target.write_bytes(b"a\r\nb\r\n")
        edit_tool().run({"path": "win.txt", "old": "b", "new": "B"}, ctx)
        assert target.read_bytes() == b"a\r\nB\r\n"

    def test_read_still_detects_images(self, ctx):
        from githi.types import ImageContent
        png = bytes([0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A]) \
            + (13).to_bytes(4, "big") + b"IHDR" + b"\x00" * 17
        (ctx.policy.root / "s.png").write_bytes(png)
        result = read_tool().run({"path": "s.png"}, ctx)
        assert any(isinstance(b, ImageContent) for b in result.content)

    def test_missing_file_is_still_an_error(self, ctx):
        assert read_tool().run({"path": "absent.txt"}, ctx).is_error

    def test_ls_still_lists(self, ctx):
        assert "notes.txt" in ls_tool().run({"path": "."}, ctx).text


class TestConfinementIsStillEnforced:
    """The env says *how* to reach a path; the policy says *whether* it may be
    reached. A backend swap must never quietly drop the second question."""

    def test_read_escape_is_refused(self, ctx):
        assert read_tool().run({"path": "../../etc/passwd"}, ctx).is_error

    def test_write_escape_is_refused(self, ctx):
        assert write_tool().run({"path": "../outside.txt", "content": "x"}, ctx).is_error

    def test_read_only_policy_still_blocks_writes(self, tmp_path, env):
        ctx = ToolContext(policy=SandboxPolicy(root=tmp_path, writable=False), env=env)
        assert write_tool().run({"path": "x.txt", "content": "y"}, ctx).is_error
        assert not env.writes, "a blocked write still reached the backend"


class TestDefaultEnv:
    def test_a_context_without_an_env_still_works(self, tmp_path):
        """Every existing caller constructs `ToolContext(policy=...)`. The env
        has to default, or the port becomes a breaking change for no reason."""
        (tmp_path / "f.txt").write_text("hello", encoding="utf-8")
        ctx = ToolContext(policy=SandboxPolicy(root=tmp_path))
        assert "hello" in read_tool().run({"path": "f.txt"}, ctx).text

    def test_the_default_env_is_rooted_at_the_sandbox(self, tmp_path):
        ctx = ToolContext(policy=SandboxPolicy(root=tmp_path))
        assert ctx.env.cwd == tmp_path.resolve()


class TestErrorsPropagate:
    def test_a_backend_failure_becomes_a_tool_error(self, tmp_path):
        """A remote backend fails in ways the local one cannot. The tool has
        to surface that as a readable error rather than raising."""

        class BrokenEnv(LocalExecutionEnv):
            def read_binary_file(self, path):
                from githi.harness.env import err
                return err(FileError(FileErrorCode.PERMISSION_DENIED,
                                     "backend refused", str(path)))

        (tmp_path / "f.txt").write_text("hello", encoding="utf-8")
        ctx = ToolContext(policy=SandboxPolicy(root=tmp_path), env=BrokenEnv(tmp_path))
        result = read_tool().run({"path": "f.txt"}, ctx)
        assert result.is_error and "refused" in result.text
