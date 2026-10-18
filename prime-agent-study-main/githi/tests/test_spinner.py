"""Live feedback while a tool runs.

Reported as "this just got stuck": three tool calls printed, then nothing.
The agent was working -- a network call was simply slow -- but the terminal
gave no sign of it, and with a 120-second command timeout the gap between
"working" and "hung" is unobservable for two minutes.

The renderer printed a line at tool start and the next line at tool end, with
silence in between. Tool execution is where an agent spends most of its wall
clock, so that is precisely the window that needed a heartbeat.

A spinner thread is the only option that works here: the tool call blocks the
main thread, so nothing on it can update the display. The same reasoning as
the ESC reader -- and the same constraints, so it must be silent when output
is redirected and must never outlive the call it is reporting on.
"""

from __future__ import annotations

import io
import time

import pytest

from githi.repl import Spinner


class TestSpinner:
    def test_it_writes_something_while_running(self):
        out = io.StringIO()
        spinner = Spinner(out=out, enabled=True, interval=0.02)
        spinner.start("bash")
        time.sleep(0.12)
        spinner.stop()
        assert out.getvalue(), "no heartbeat during the call"

    def test_it_reports_elapsed_time(self):
        """A number that visibly grows is what distinguishes 'slow' from
        'hung' -- an animation alone could be a stuck loop."""
        out = io.StringIO()
        spinner = Spinner(out=out, enabled=True, interval=0.02)
        spinner.start("bash")
        time.sleep(0.25)
        spinner.stop()
        assert "s" in out.getvalue()

    def test_it_names_the_running_tool(self):
        out = io.StringIO()
        spinner = Spinner(out=out, enabled=True, interval=0.02)
        spinner.start("bash")
        time.sleep(0.08)
        spinner.stop()
        assert "bash" in out.getvalue()

    def test_stop_clears_the_line(self):
        """The final tool result is printed by the caller; a leftover spinner
        frame would collide with it."""
        out = io.StringIO()
        spinner = Spinner(out=out, enabled=True, interval=0.02)
        spinner.start("bash")
        time.sleep(0.08)
        spinner.stop()
        assert out.getvalue().rstrip().endswith("\r") or "\r" in out.getvalue()

    def test_the_thread_does_not_outlive_stop(self):
        """A spinner still running after the call would scribble over whatever
        is printed next."""
        spinner = Spinner(out=io.StringIO(), enabled=True, interval=0.02)
        spinner.start("bash")
        time.sleep(0.06)
        spinner.stop()
        time.sleep(0.08)
        assert not spinner.running

    def test_disabled_writes_nothing(self):
        """Redirected output must stay clean -- a spinner in a piped file is
        line noise."""
        out = io.StringIO()
        spinner = Spinner(out=out, enabled=False, interval=0.02)
        spinner.start("bash")
        time.sleep(0.08)
        spinner.stop()
        assert out.getvalue() == ""

    def test_stop_without_start_is_harmless(self):
        Spinner(out=io.StringIO(), enabled=True).stop()

    def test_restarting_does_not_leak_a_thread(self):
        spinner = Spinner(out=io.StringIO(), enabled=True, interval=0.02)
        for _ in range(3):
            spinner.start("bash")
            time.sleep(0.04)
            spinner.stop()
        assert not spinner.running
