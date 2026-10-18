"""One-shot mode should stream the answer too.

The REPL renders text as it arrives; one-shot did not. So `githi -v "..."`
printed each tool call, then went completely silent for the ten to thirty
seconds the model spent writing the answer, then dumped it all at once. That
is indistinguishable from a hang -- which is exactly what it was reported as.

The cause was a gap between two things I built separately: the loop emits
`TEXT_DELTA` events, and one-shot's `-v` printer only handled tool events.
Streaming existed and nothing consumed it.

The fix has to avoid printing the answer twice, since one-shot also printed
`result.output` at the end. Streamed text goes to stdout (so piping still
works), and the final print is skipped when anything was streamed.
"""

from __future__ import annotations

import io

import pytest

from githi.agent.events import Event, EventType
from githi.cli import StreamPrinter


class TestStreamPrinter:
    def test_text_deltas_are_written_as_they_arrive(self):
        out = io.StringIO()
        printer = StreamPrinter(out=out, verbose=False)
        for piece in ("Hel", "lo"):
            printer(Event(EventType.TEXT_DELTA, {"delta": piece}))
        assert out.getvalue() == "Hello"

    def test_it_records_that_something_streamed(self):
        """The flag the caller uses to skip re-printing the answer."""
        printer = StreamPrinter(out=io.StringIO(), verbose=False)
        assert not printer.streamed
        printer(Event(EventType.TEXT_DELTA, {"delta": "x"}))
        assert printer.streamed

    def test_tool_calls_are_quiet_unless_verbose(self):
        """Without -v the answer should be the only thing on stdout, so it can
        be piped into another command."""
        out = io.StringIO()
        printer = StreamPrinter(out=out, verbose=False)
        printer(Event(EventType.TOOL_START, {"tool": "bash", "args": {}}))
        assert out.getvalue() == ""

    def test_verbose_reports_tool_calls(self):
        err = io.StringIO()
        printer = StreamPrinter(out=io.StringIO(), err=err, verbose=True)
        printer(Event(EventType.TOOL_START, {"tool": "bash", "args": {"command": "ls"}}))
        assert "bash" in err.getvalue()

    def test_tool_activity_goes_to_stderr(self):
        """So `githi "..." > answer.txt` captures the answer and nothing else."""
        out, err = io.StringIO(), io.StringIO()
        printer = StreamPrinter(out=out, err=err, verbose=True)
        printer(Event(EventType.TOOL_START, {"tool": "bash", "args": {}}))
        printer(Event(EventType.TEXT_DELTA, {"delta": "answer"}))
        assert out.getvalue() == "answer"
        assert "bash" in err.getvalue()

    def test_a_tool_call_breaks_a_streamed_line(self):
        """Without the break, the tool line lands mid-sentence."""
        out, err = io.StringIO(), io.StringIO()
        printer = StreamPrinter(out=out, err=err, verbose=True)
        printer(Event(EventType.TEXT_DELTA, {"delta": "thinking"}))
        printer(Event(EventType.TOOL_START, {"tool": "bash", "args": {}}))
        assert out.getvalue().endswith("\n")

    def test_errors_are_marked(self):
        err = io.StringIO()
        printer = StreamPrinter(out=io.StringIO(), err=err, verbose=True)
        printer(Event(EventType.TOOL_END, {"tool": "bash", "is_error": True}))
        assert "error" in err.getvalue()
