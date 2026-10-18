"""The bash tool.

Thin over `sandbox.runner` on purpose -- the interesting behaviour is tested
there. What is tested here is the *tool contract*: argument validation, how
failures are surfaced to the model, and that the policy is honoured.
"""

from __future__ import annotations

import threading

import pytest

from githi.sandbox.policy import SandboxPolicy
from githi.tools.base import ToolContext
from githi.tools.bash import bash_tool


@pytest.fixture
def run(tmp_path):
    tool = bash_tool()
    ctx = ToolContext(policy=SandboxPolicy(root=tmp_path, timeout_s=5))
    return lambda **args: tool.run(args, ctx)


class TestContract:
    def test_returns_stdout(self, run):
        assert "hello" in run(command="echo hello").text

    def test_missing_command_is_a_correctable_error(self, run):
        assert run().is_error is True

    def test_empty_command_is_a_correctable_error(self, run):
        assert run(command="  ").is_error is True

    def test_nonzero_exit_is_an_error_result(self, run):
        result = run(command="exit 4")
        assert result.is_error is True
        assert "4" in result.text

    def test_stderr_reaches_the_model(self, run):
        assert "warn" in run(command="echo warn >&2").text

    def test_no_output_still_says_something(self, run):
        """A blank tool result gives the model nothing to reason about."""
        assert run(command="true").text.strip() != ""


class TestSafety:
    def test_credentials_are_not_visible(self, run, monkeypatch):
        monkeypatch.setenv("GITHUB_TOKEN", "ghp_secret_value")
        assert "ghp_secret_value" not in run(command="env").text

    def test_timeout_is_reported_clearly(self, tmp_path):
        tool = bash_tool()
        ctx = ToolContext(policy=SandboxPolicy(root=tmp_path, timeout_s=1))
        result = tool.run({"command": "sleep 20"}, ctx)
        assert result.is_error and "limit" in result.text.lower()

    def test_model_is_told_when_its_timeout_was_reduced(self, tmp_path):
        """Otherwise it debugs the wrong problem -- it sees a killed command
        and concludes the work is too slow, never learning its own request
        was overridden."""
        from githi.sandbox.policy import SandboxPolicy as P
        tool = bash_tool()
        ctx = ToolContext(policy=P(root=tmp_path, timeout_s=1, max_timeout_s=2))
        result = tool.run({"command": "sleep 30", "timeout": 9999}, ctx)
        assert "2" in result.text, "must state the ceiling actually applied"

    def test_per_call_timeout_cannot_exceed_policy_cap(self, tmp_path):
        """A model-supplied timeout is a request, not an override."""
        tool = bash_tool()
        ctx = ToolContext(policy=SandboxPolicy(root=tmp_path, timeout_s=1, max_timeout_s=2))
        result = tool.run({"command": "sleep 30", "timeout": 9999}, ctx)
        assert result.is_error and result.text

    def test_interrupt_is_not_reported_as_a_bash_failure(self, tmp_path):
        interrupted = threading.Event()
        interrupted.set()
        ctx = ToolContext(policy=SandboxPolicy(root=tmp_path, timeout_s=5),
                          extra={"interrupt": interrupted})
        result = bash_tool().run({"command": "sleep 30"}, ctx)
        assert result.is_error is False
        assert "interrupt" in result.text.lower()


class TestSpill:
    def test_truncated_output_tells_the_model_where_the_rest_is(self, tmp_path):
        from githi.sandbox.policy import SandboxPolicy as P
        tool = bash_tool()
        ctx = ToolContext(policy=P(root=tmp_path, max_output_bytes=400, timeout_s=20))
        content = tool.run({"command": "python3 -c \"print('A'*9000)\""}, ctx).text
        assert "truncated" in content.lower()
        # The path must be named, or the spill file might as well not exist.
        assert ".githi" in content or "output" in content.lower()

    def test_the_named_path_is_actually_readable(self, tmp_path):
        """End to end: the path bash reports must work verbatim in `read`."""
        import re

        from githi.sandbox.policy import SandboxPolicy as P
        from githi.tools.read import read_tool

        ctx = ToolContext(policy=P(root=tmp_path, max_output_bytes=400, timeout_s=20))
        content = bash_tool().run(
            {"command": "python3 -c \"print('NEEDLE'); print('A'*9000)\""}, ctx).text
        match = re.search(r"[\w./-]*\.githi[\w./-]*", content)
        assert match, f"no spill path found in: {content[:200]}"
        assert "NEEDLE" in read_tool().run({"path": match.group(0)}, ctx).text


class TestSchema:
    def test_declares_command_required(self):
        params = bash_tool().parameters
        assert params["required"] == ["command"]
        assert "timeout" in params["properties"]

    def test_description_is_mechanical_like_pis(self):
        """Pi's bash description says what the tool does, what it returns and
        what happens on truncation -- nothing about networks, credentials or
        strategy. The earlier version warned that network calls "cannot
        succeed", which the model read as "the network is closed"."""
        d = bash_tool().description.lower()
        assert "stdout" in d and "stderr" in d and "timeout" in d
        assert "cannot succeed" not in d and "do not waste" not in d

    def test_description_mentions_the_spill_file(self):
        """Pi: "If truncated, full output is saved to a temp file." A spill
        file the model is not told about might as well not exist."""
        assert "truncat" in bash_tool().description.lower()
        assert "saved" in bash_tool().description.lower()
