"""`Result`, the error taxonomy, and `ExecutionEnv` (port of `harness/types.ts`).

Three connected ideas from pi:

* **`Result[T, E]`** — filesystem and shell operations return failure as a
  *value*, not an exception. A tool that must turn every error into a model-
  readable message ends up wrapping everything in try/except anyway; making
  it explicit means the failure cannot be forgotten.
* **Backend-independent error codes.** `not_found` means the same thing
  whether the file was missing locally, in a container, or over SSH. Without
  a code, callers pattern-match on message text, which breaks per backend.
* **`ExecutionEnv = FileSystem + Shell`** — the abstraction that lets the
  *same* tools run somewhere else. githi's tools currently hardcode the local
  filesystem and `subprocess`, which is precisely why its sandbox cannot be a
  container. This is the seam that would make that possible.
"""

from __future__ import annotations

import threading

import pytest

from githi.harness.env import (
    ExecutionError,
    ExecutionErrorCode,
    FileError,
    FileErrorCode,
    LocalExecutionEnv,
    Result,
    err,
    get_or_raise,
    ok,
)


class TestResult:
    def test_ok_carries_a_value(self):
        result = ok(42)
        assert result.is_ok and result.value == 42

    def test_err_carries_an_error(self):
        result: Result[int, str] = err("boom")
        assert not result.is_ok and result.error == "boom"

    def test_ok_is_falsy_safe(self):
        """`ok(0)` and `ok("")` must still read as success -- testing the
        Result rather than its value is the whole point."""
        assert ok(0).is_ok and ok("").is_ok and ok(False).is_ok

    def test_get_or_raise_unwraps(self):
        assert get_or_raise(ok("value")) == "value"

    def test_get_or_raise_raises_on_error(self):
        with pytest.raises(FileError):
            get_or_raise(err(FileError("not_found", "missing", path="/x")))

    def test_unwrap_or_supplies_a_default(self):
        assert err(FileError("not_found", "x")).unwrap_or("fallback") == "fallback"
        assert ok("real").unwrap_or("fallback") == "real"


class TestErrorCodes:
    def test_file_error_codes_are_backend_independent(self):
        assert {c.value for c in FileErrorCode} >= {
            "aborted", "not_found", "permission_denied", "not_directory",
            "is_directory", "invalid", "not_supported", "unknown"}

    def test_execution_error_codes(self):
        assert {c.value for c in ExecutionErrorCode} >= {
            "aborted", "timeout", "shell_unavailable", "spawn_error", "unknown"}

    def test_file_error_carries_code_and_path(self):
        error = FileError("not_found", "no such file", path="/tmp/x")
        assert error.code is FileErrorCode.NOT_FOUND and error.path == "/tmp/x"

    def test_code_survives_as_an_enum_from_a_string(self):
        """Callers branch on the code; accepting the string form keeps call
        sites readable without giving up the closed set."""
        assert FileError("permission_denied", "x").code is FileErrorCode.PERMISSION_DENIED

    def test_unknown_code_does_not_explode(self):
        """A backend reporting something unmapped must degrade to `unknown`,
        not crash the tool that was trying to report a failure."""
        assert FileError("something_new", "x").code is FileErrorCode.UNKNOWN


class TestLocalFileSystem:
    def test_read_text_file(self, tmp_path):
        (tmp_path / "a.txt").write_text("hello")
        env = LocalExecutionEnv(cwd=tmp_path)
        assert env.read_text_file("a.txt").value == "hello"

    def test_missing_file_returns_not_found(self, tmp_path):
        result = LocalExecutionEnv(cwd=tmp_path).read_text_file("absent.txt")
        assert not result.is_ok and result.error.code is FileErrorCode.NOT_FOUND

    def test_reading_a_directory_reports_is_directory(self, tmp_path):
        (tmp_path / "sub").mkdir()
        result = LocalExecutionEnv(cwd=tmp_path).read_text_file("sub")
        assert result.error.code is FileErrorCode.IS_DIRECTORY

    def test_write_then_read(self, tmp_path):
        env = LocalExecutionEnv(cwd=tmp_path)
        assert env.write_file("out.txt", "data").is_ok
        assert env.read_text_file("out.txt").value == "data"

    def test_write_creates_parents(self, tmp_path):
        env = LocalExecutionEnv(cwd=tmp_path)
        env.write_file("deep/nested/f.txt", "x")
        assert (tmp_path / "deep" / "nested" / "f.txt").exists()

    def test_append(self, tmp_path):
        env = LocalExecutionEnv(cwd=tmp_path)
        env.write_file("a.txt", "one")
        env.append_file("a.txt", "-two")
        assert env.read_text_file("a.txt").value == "one-two"

    def test_exists(self, tmp_path):
        (tmp_path / "a.txt").write_text("x")
        env = LocalExecutionEnv(cwd=tmp_path)
        assert env.exists("a.txt").value is True
        assert env.exists("absent").value is False

    def test_list_dir(self, tmp_path):
        (tmp_path / "a.txt").write_text("x")
        (tmp_path / "sub").mkdir()
        entries = LocalExecutionEnv(cwd=tmp_path).list_dir(".").value
        assert {e.name for e in entries} == {"a.txt", "sub"}
        assert {e.kind for e in entries} == {"file", "directory"}

    def test_list_dir_on_a_file_reports_not_directory(self, tmp_path):
        (tmp_path / "a.txt").write_text("x")
        result = LocalExecutionEnv(cwd=tmp_path).list_dir("a.txt")
        assert result.error.code is FileErrorCode.NOT_DIRECTORY

    def test_absolute_path_resolves_against_cwd(self, tmp_path):
        env = LocalExecutionEnv(cwd=tmp_path)
        assert env.absolute_path("rel.txt").value == str(tmp_path / "rel.txt")

    def test_create_and_remove_dir(self, tmp_path):
        env = LocalExecutionEnv(cwd=tmp_path)
        env.create_dir("d/e", recursive=True)
        assert (tmp_path / "d" / "e").is_dir()
        env.remove("d", recursive=True)
        assert not (tmp_path / "d").exists()

    def test_removing_a_missing_path_is_an_error_unless_forced(self, tmp_path):
        env = LocalExecutionEnv(cwd=tmp_path)
        assert not env.remove("absent").is_ok
        assert env.remove("absent", force=True).is_ok


class TestLocalShell:
    def test_exec_captures_stdout_and_exit_code(self, tmp_path):
        result = LocalExecutionEnv(cwd=tmp_path).exec("echo hello")
        assert result.is_ok and result.value.stdout.strip() == "hello"
        assert result.value.exit_code == 0

    def test_nonzero_exit_is_a_value_not_an_error(self, tmp_path):
        """A failing command ran successfully -- the *execution* worked. Only
        an inability to run it at all is an ExecutionError."""
        result = LocalExecutionEnv(cwd=tmp_path).exec("exit 3")
        assert result.is_ok and result.value.exit_code == 3

    def test_stderr_is_captured(self, tmp_path):
        result = LocalExecutionEnv(cwd=tmp_path).exec("echo oops >&2")
        assert "oops" in result.value.stderr

    def test_timeout_reports_the_timeout_code(self, tmp_path):
        result = LocalExecutionEnv(cwd=tmp_path).exec("sleep 20", timeout=1)
        assert not result.is_ok and result.error.code is ExecutionErrorCode.TIMEOUT

    def test_runs_in_the_env_cwd(self, tmp_path):
        assert str(tmp_path) in LocalExecutionEnv(cwd=tmp_path).exec("pwd").value.stdout

    def test_env_overrides_are_applied(self, tmp_path):
        result = LocalExecutionEnv(cwd=tmp_path).exec("echo $GITHI_TEST",
                                                      env={"GITHI_TEST": "marker"})
        assert "marker" in result.value.stdout

    def test_exec_streams_output_before_completion(self, tmp_path):
        updates: list[tuple[str, str]] = []
        result = LocalExecutionEnv(cwd=tmp_path).exec(
            "echo first; echo second >&2",
            on_output=lambda stream, text: updates.append((stream, text)),
        )
        assert result.is_ok
        assert ("stdout", "first\n") in updates
        assert ("stderr", "second\n") in updates

    def test_interrupt_kills_the_process_group_and_keeps_partial_output(self, tmp_path):
        interrupted = threading.Event()
        updates: list[str] = []

        def stop_after_output(stream: str, text: str) -> None:
            updates.append(text)
            if "ready" in text:
                interrupted.set()

        result = LocalExecutionEnv(cwd=tmp_path).exec(
            "echo ready; sleep 30",
            interrupt=interrupted,
            on_output=stop_after_output,
        )
        assert not result.is_ok and result.error.code is ExecutionErrorCode.ABORTED
        assert "ready" in result.error.partial_stdout
        assert any("ready" in text for text in updates)


def test_execution_env_satisfies_both_protocols(tmp_path):
    """`ExecutionEnv = FileSystem + Shell`. One object, so a tool needs a
    single dependency to be relocatable to a container or remote host."""
    from githi.harness.env import ExecutionEnv

    assert isinstance(LocalExecutionEnv(cwd=tmp_path), ExecutionEnv)
