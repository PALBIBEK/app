"""Subprocess execution under the policy.

The runner is the only place in githi that spawns a process, so every
resource bound (time, output size, process group) is enforced here once
rather than trusted to each caller.
"""

from __future__ import annotations

import threading

import pytest

from githi.sandbox.policy import SandboxPolicy
from githi.sandbox.runner import run_command


@pytest.fixture
def policy(tmp_path):
    return SandboxPolicy(root=tmp_path, timeout_s=5)


class TestExecution:
    def test_captures_stdout_and_exit_code(self, policy):
        r = run_command("echo hello", policy)
        assert r.stdout.strip() == "hello"
        assert r.exit_code == 0 and r.timed_out is False

    def test_captures_stderr(self, policy):
        assert "boom" in run_command("echo boom >&2", policy).stderr

    def test_nonzero_exit_is_reported_not_raised(self, policy):
        """A failing command is information for the model, not a crash."""
        assert run_command("exit 7", policy).exit_code == 7

    def test_interrupt_returns_partial_output(self, policy):
        interrupted = threading.Event()
        updates: list[str] = []

        def stop_after_first_line(stream: str, text: str) -> None:
            updates.append(text)
            interrupted.set()

        result = run_command("echo ready; sleep 30", policy,
                             interrupt=interrupted, on_output=stop_after_first_line)
        assert result.interrupted is True
        assert "ready" in result.stdout
        assert any("ready" in text for text in updates)

    def test_runs_inside_the_sandbox_root(self, policy):
        assert str(policy.root) in run_command("pwd", policy).stdout

    def test_pipelines_work(self, policy):
        """The whole value of a shell is chaining -- one call doing the work
        of many tool round trips."""
        out = run_command("printf 'b\\na\\nb\\n' | sort | uniq -c | sort -rn", policy).stdout
        assert "2 b" in out.replace("  ", " ")


class TestLimits:
    def test_timeout_kills_the_command(self, tmp_path):
        r = run_command("sleep 30", SandboxPolicy(root=tmp_path, timeout_s=1))
        assert r.timed_out is True and r.exit_code != 0

    def test_timeout_kills_the_whole_process_group(self, tmp_path):
        """A backgrounded child must not outlive the timeout -- otherwise a
        killed command leaves work running that nothing is tracking."""
        marker = tmp_path / "orphan.txt"
        r = run_command(f"(sleep 4 && touch {marker}) & sleep 30",
                        SandboxPolicy(root=tmp_path, timeout_s=1))
        assert r.timed_out is True
        import time
        time.sleep(5)
        assert not marker.exists(), "background child survived the timeout"

    def test_output_is_capped(self, tmp_path):
        p = SandboxPolicy(root=tmp_path, max_output_bytes=500)
        r = run_command("python3 -c \"print('A'*10000)\"", p)
        assert r.truncated is True
        assert len(r.stdout) <= 500 + 200        # cap plus the elision notice

    def test_truncation_keeps_the_tail(self, tmp_path):
        """Results usually land at the end of output; keeping only the head
        would discard exactly the part that matters."""
        p = SandboxPolicy(root=tmp_path, max_output_bytes=400)
        r = run_command("python3 -c \"print('A'*5000); print('FINAL_ANSWER')\"", p)
        assert "FINAL_ANSWER" in r.stdout

    def test_output_within_the_cap_is_untouched(self, policy):
        r = run_command("echo small", policy)
        assert r.truncated is False and r.stdout.strip() == "small"


class TestTimeoutCeiling:
    """The ceiling must fit the work, and clamping must be visible.

    Measured: a full `git clone` of langchain takes 188s. With the old
    120s ceiling githi could never finish one, so it fell back to
    `--depth 100` -- rational under the budget, but shallow history cannot
    answer "which commit introduced this line", and `git blame` on a shallow
    clone silently misattributes to the oldest grafted commit. It reported a
    wrong commit as a result. Pi has no default timeout and cloned fine.
    """

    def test_ceiling_accommodates_a_large_clone(self, tmp_path):
        p = SandboxPolicy(root=tmp_path, timeout_s=300)
        assert p.timeout_s >= 200, "must fit a ~190s clone"

    def test_clamping_is_reported_not_silent(self, tmp_path):
        """The old clamp was invisible: a request for 180s became 120s with
        no signal, so a killed command looked like a slow command."""
        p = SandboxPolicy(root=tmp_path, timeout_s=99_999)
        assert p.timeout_s == p.max_timeout_s
        assert p.timeout_was_clamped is True

    def test_unclamped_request_is_not_flagged(self, tmp_path):
        assert SandboxPolicy(root=tmp_path, timeout_s=10).timeout_was_clamped is False


class TestSpillFile:
    """Truncated output must remain *reachable*, not be destroyed.

    Eliding the middle of a 100KB result and discarding it means the model
    cannot get at that data by any route -- it has to re-run the command and
    hope a narrower filter catches what it needs. Writing the full output to
    a file inside the sandbox turns an irreversible loss into one `grep`.
    """

    def test_no_spill_when_output_fits(self, policy):
        assert run_command("echo small", policy).spill_path is None

    def test_spill_created_when_truncated(self, tmp_path):
        p = SandboxPolicy(root=tmp_path, max_output_bytes=400)
        r = run_command("python3 -c \"print('A'*9000)\"", p)
        assert r.truncated is True and r.spill_path

    def test_spill_holds_the_complete_untruncated_output(self, tmp_path):
        p = SandboxPolicy(root=tmp_path, max_output_bytes=400)
        r = run_command("python3 -c \"print('HEAD'); print('B'*9000); print('TAIL')\"", p)
        full = (tmp_path / r.spill_path).read_text()
        assert "HEAD" in full and "TAIL" in full
        assert full.count("B") == 9000, "the elided middle must survive on disk"

    def test_spill_path_is_relative_to_the_sandbox_root(self, tmp_path):
        """It has to be usable as-is by `read` and `grep`, which resolve
        relative to root. An absolute host path would be rejected by them."""
        p = SandboxPolicy(root=tmp_path, max_output_bytes=400)
        r = run_command("python3 -c \"print('A'*9000)\"", p)
        assert not r.spill_path.startswith("/")
        assert (tmp_path / r.spill_path).is_file()

    def test_repeated_spills_do_not_collide(self, tmp_path):
        p = SandboxPolicy(root=tmp_path, max_output_bytes=400)
        a = run_command("python3 -c \"print('A'*9000)\"", p)
        b = run_command("python3 -c \"print('B'*9000)\"", p)
        assert a.spill_path != b.spill_path
        assert "B" in (tmp_path / b.spill_path).read_text()

    def test_read_only_policy_does_not_write_a_spill(self, tmp_path):
        """`--read-only` means no writes -- including ours. Truncation still
        happens, it just degrades to the old behaviour."""
        p = SandboxPolicy(root=tmp_path, max_output_bytes=400, writable=False)
        r = run_command("python3 -c \"print('A'*9000)\"", p)
        assert r.truncated is True and r.spill_path is None


class TestIsolation:
    def test_credentials_are_absent(self, policy, monkeypatch):
        monkeypatch.setenv("GITHUB_TOKEN", "ghp_leaked")
        r = run_command("echo \"[${GITHUB_TOKEN:-EMPTY}]\"", policy)
        assert "ghp_leaked" not in r.stdout and "[EMPTY]" in r.stdout

    def test_cannot_write_outside_root_via_relative_escape(self, policy):
        """Not enforced by the kernel -- this documents the honest limit:
        `run_command` sets cwd, it does not chroot. Callers that need real
        containment of arbitrary commands need a container."""
        r = run_command("cd / && pwd", policy)
        assert r.stdout.strip() == "/"


def test_empty_command_is_rejected(policy):
    with pytest.raises(ValueError):
        run_command("   ", policy)
