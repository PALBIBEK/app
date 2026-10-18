"""Command output must be safe to put in a transcript and on a screen.

Found by probing, not by any test: `printf 'a\\x00\\x07\\x1b[31mb'` puts a NUL,
a BEL and an ANSI colour escape straight into the transcript. pi sanitises
this in `shell-output.ts`; githi did not.

Three separate harms, which is why the fix is not merely cosmetic:

* **NUL** is not representable in JSON strings by several providers, so a
  command that emits one can turn the *next* request into a 400 -- an error
  that surfaces far from the command that caused it.
* **ANSI escapes** are about to matter much more than they did. With a
  terminal UI, output containing `\\x1b[2J` clears the user's screen and
  `\\x1b[31m` leaves everything after it red. The agent would be handing a
  remote repository's file contents direct control of the display.
* **Tokens.** Escape sequences are meaningless to the model and are billed
  like any other text.

Newlines and tabs are kept: they are structure, not noise, and stripping them
would run every line of output together.
"""

from __future__ import annotations

import pytest

from githi.harness.shell_output import sanitize_output
from githi.sandbox.policy import SandboxPolicy
from githi.tools.base import ToolContext
from githi.tools.bash import bash_tool


class TestSanitize:
    def test_nul_is_removed(self):
        assert "\x00" not in sanitize_output("a\x00b")

    def test_bell_is_removed(self):
        assert "\x07" not in sanitize_output("a\x07b")

    def test_ansi_colour_is_stripped(self):
        assert sanitize_output("\x1b[31mred\x1b[0m") == "red"

    def test_screen_clearing_is_stripped(self):
        """`\\x1b[2J` would wipe the user's terminal."""
        assert "\x1b" not in sanitize_output("\x1b[2Jgone")

    def test_cursor_movement_is_stripped(self):
        assert sanitize_output("\x1b[1;1Hx") == "x"

    def test_osc_sequences_are_stripped(self):
        """OSC can retitle the window or, on some terminals, read the
        clipboard -- it must never survive from command output."""
        assert "title" not in sanitize_output("\x1b]0;title\x07after")

    def test_newlines_survive(self):
        assert sanitize_output("a\nb\n") == "a\nb\n"

    def test_tabs_survive(self):
        assert sanitize_output("a\tb") == "a\tb"

    def test_carriage_returns_survive(self):
        """Progress output uses CR heavily; the renderer decides what to do
        with it, but the data should reach it intact."""
        assert "\r" in sanitize_output("50%\r100%")

    def test_ordinary_text_is_untouched(self):
        assert sanitize_output("hello world") == "hello world"

    def test_unicode_is_untouched(self):
        assert sanitize_output("héllo → 世界") == "héllo → 世界"

    def test_empty_input(self):
        assert sanitize_output("") == ""


class TestBashToolIsSanitized:
    def test_control_bytes_do_not_reach_the_transcript(self, tmp_path):
        ctx = ToolContext(policy=SandboxPolicy(root=tmp_path))
        result = bash_tool().run(
            {"command": r"printf 'a\x00\x07\x1b[31mb'"}, ctx)
        assert "\x00" not in result.text
        assert "\x07" not in result.text
        assert "\x1b" not in result.text

    def test_the_actual_output_still_arrives(self, tmp_path):
        ctx = ToolContext(policy=SandboxPolicy(root=tmp_path))
        result = bash_tool().run({"command": r"printf 'a\x1b[31mb'"}, ctx)
        assert "ab" in result.text

    def test_multiline_output_keeps_its_lines(self, tmp_path):
        ctx = ToolContext(policy=SandboxPolicy(root=tmp_path))
        result = bash_tool().run({"command": "printf 'one\\ntwo\\n'"}, ctx)
        assert "one\ntwo" in result.text

    def test_stderr_is_sanitized_too(self, tmp_path):
        """Compilers and test runners colourise stderr by default, so this is
        the *more* likely source of escapes, not the less."""
        ctx = ToolContext(policy=SandboxPolicy(root=tmp_path))
        result = bash_tool().run(
            {"command": r"printf '\x1b[31merr\x1b[0m' >&2; exit 1"}, ctx)
        assert "\x1b" not in result.text
