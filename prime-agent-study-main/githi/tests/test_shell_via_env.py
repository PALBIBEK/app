"""The Shell half of `ExecutionEnv`.

`ExecutionEnv` is FileSystem **+ Shell**. Wiring only the filesystem half
leaves `bash` -- the tool that does the most, and the one a sandbox exists to
contain -- spawning processes directly, so a container backend would relocate
`read` and `write` while `bash` kept running on the host. That is worse than
no abstraction: it looks contained and is not.

The split of responsibility:

* **`LocalExecutionEnv.exec`** owns *spawning* -- process group, timeout, kill.
* **`run_command`** keeps everything policy-shaped: the redacted child
  environment, output truncation, and the spill file. None of that is
  backend-specific, and duplicating it into each backend is how one of them
  ends up quietly leaking secrets.

The process-group test is the one that matters. `subprocess.run` raises
`TimeoutExpired`, which carries cmd, timeout and output but **no pid** -- so
there is nothing to signal, and anything the command backgrounded outlives
the kill and keeps running untracked. `run_command` was written with `Popen`
for exactly this reason; `LocalExecutionEnv.exec` was not, and reintroduced
the bug one file away from the comment explaining it.
"""

from __future__ import annotations

import os
import signal
import time

import pytest

from githi.harness.env import ExecutionErrorCode, LocalExecutionEnv
from githi.sandbox.policy import SandboxPolicy
from githi.sandbox.runner import run_command
from githi.tools.base import ToolContext
from githi.tools.bash import bash_tool


class TestExecKillsTheProcessGroup:
    def test_timeout_reports_the_timeout_code(self, tmp_path):
        result = LocalExecutionEnv(tmp_path).exec("sleep 20", timeout=1)
        assert not result.is_ok
        assert result.error.code is ExecutionErrorCode.TIMEOUT

    def test_a_backgrounded_child_does_not_survive_the_timeout(self, tmp_path):
        """The orphan check. A killed command whose grandchildren keep running
        leaves untracked processes holding the sandbox open."""
        marker = tmp_path / "orphan.pid"
        env = LocalExecutionEnv(tmp_path)
        env.exec(f"(sleep 30 & echo $! > {marker}); sleep 20", timeout=2)

        assert marker.exists(), "the test command did not record a pid"
        orphan = int(marker.read_text().strip())

        time.sleep(0.4)                      # let the signal land
        with pytest.raises(OSError):
            # signal 0 only probes for existence; it raises once the pid is gone.
            os.kill(orphan, 0)

    def test_a_fast_command_is_unaffected(self, tmp_path):
        result = LocalExecutionEnv(tmp_path).exec("echo hi", timeout=10)
        assert result.is_ok and result.value.stdout.strip() == "hi"


class TestRunCommandUsesTheEnv:
    def test_run_command_accepts_an_env(self, tmp_path):
        policy = SandboxPolicy(root=tmp_path)
        result = run_command("echo routed", policy, env=LocalExecutionEnv(tmp_path))
        assert "routed" in result.stdout

    def test_the_spawn_is_delegated(self, tmp_path):
        """Substituting the backend must change where the command runs. A
        recording env proves the indirection exists; asserting on output alone
        would pass either way."""
        seen: list[str] = []

        class RecordingEnv(LocalExecutionEnv):
            def exec(self, command, **kw):
                seen.append(command)
                return super().exec(command, **kw)

        run_command("echo hi", SandboxPolicy(root=tmp_path), env=RecordingEnv(tmp_path))
        assert seen == ["echo hi"]

    def test_without_an_env_it_still_works(self, tmp_path):
        """Existing callers pass only a policy."""
        assert "hi" in run_command("echo hi", SandboxPolicy(root=tmp_path)).stdout


class TestPolicyStillApplies:
    """Everything policy-shaped stays in `run_command`, so no backend can
    accidentally omit it."""

    def test_secrets_are_still_redacted_from_the_child_environment(self, tmp_path):
        policy = SandboxPolicy(root=tmp_path, extra_secrets=("SUPER_SECRET",))
        os.environ["SUPER_SECRET"] = "hunter2"
        try:
            result = run_command("echo ${SUPER_SECRET:-absent}", policy,
                                 env=LocalExecutionEnv(tmp_path))
            assert "hunter2" not in result.stdout
        finally:
            os.environ.pop("SUPER_SECRET", None)

    def test_output_is_still_truncated(self, tmp_path):
        policy = SandboxPolicy(root=tmp_path, max_output_bytes=500)
        result = run_command("head -c 20000 /dev/zero | tr '\\0' 'x'", policy,
                             env=LocalExecutionEnv(tmp_path))
        assert result.truncated and len(result.stdout) < 20000

    def test_the_spill_file_still_records_the_full_output(self, tmp_path):
        policy = SandboxPolicy(root=tmp_path, max_output_bytes=500)
        result = run_command("head -c 20000 /dev/zero | tr '\\0' 'x'", policy,
                             env=LocalExecutionEnv(tmp_path))
        assert result.spill_path
        assert len((tmp_path / result.spill_path).read_text()) > 500

    def test_the_command_runs_in_the_sandbox_root(self, tmp_path):
        result = run_command("pwd", SandboxPolicy(root=tmp_path),
                             env=LocalExecutionEnv(tmp_path))
        assert str(tmp_path) in result.stdout

    def test_timeout_still_reports_exit_124(self, tmp_path):
        policy = SandboxPolicy(root=tmp_path, timeout_s=1)
        result = run_command("sleep 20", policy, env=LocalExecutionEnv(tmp_path))
        assert result.timed_out and result.exit_code == 124


class TestBashToolRoutesThroughTheEnv:
    def test_the_bash_tool_passes_its_env(self, tmp_path):
        seen: list[str] = []

        class RecordingEnv(LocalExecutionEnv):
            def exec(self, command, **kw):
                seen.append(command)
                return super().exec(command, **kw)

        ctx = ToolContext(policy=SandboxPolicy(root=tmp_path),
                          env=RecordingEnv(tmp_path))
        bash_tool().run({"command": "echo hi"}, ctx)
        assert seen, "bash bypassed the ExecutionEnv"

    def test_the_bash_tool_still_returns_output(self, tmp_path):
        ctx = ToolContext(policy=SandboxPolicy(root=tmp_path))
        assert "hi" in bash_tool().run({"command": "echo hi"}, ctx).text
