"""Making command output safe to store and to display.

Command output is the one place where bytes chosen by *someone else* -- a
repository's build script, a file being catted -- flow into both the model's
context and the user's terminal. Two consequences follow.

**A NUL byte can break the next request.** Several providers cannot represent
it in a JSON string, so a command that emits one turns a later call into a
400: an error that surfaces nowhere near the command that caused it.

**Escape sequences are display commands, not text.** `\\x1b[2J` clears the
screen, `\\x1b[31m` colours everything after it, and OSC can retitle the
window or, on some terminals, address the clipboard. Passing them through
hands a remote repository direct control of the user's display. That was
merely untidy while githi printed to a pipe; with a terminal UI it is a real
capability leak.

Newlines, tabs and carriage returns are kept. They are structure -- stripping
them would run every line of output together, and progress output that uses
CR would become unreadable. What to *do* with a CR is the renderer's
decision; the data should reach it intact.
"""

from __future__ import annotations

import re

# CSI (colour, cursor movement, screen clearing), OSC (window title, clipboard)
# terminated by BEL or ST, and the two-character escape forms.
_ANSI = re.compile(
    r"""
    \x1b \[ [0-?]* [ -/]* [@-~]      # CSI ... final byte
    | \x1b \] .*? (?: \x07 | \x1b\\ ) # OSC ... BEL or ST
    | \x1b [@-Z\\-_]                  # two-character escapes
    """,
    re.VERBOSE | re.DOTALL,
)

# Everything below 0x20 except \t \n \r, plus DEL. Written as an explicit
# range rather than `str.isprintable` so that Unicode -- which is ordinary
# output, not noise -- is never touched.
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def sanitize_output(text: str) -> str:
    """Strip terminal control sequences and unprintable bytes."""
    if not text:
        return text
    # ANSI first: its sequences *contain* control characters (ESC, and the BEL
    # that terminates OSC). Removing those first would leave the printable
    # remainder -- `[31m`, `0;title` -- as literal visible garbage.
    return _CONTROL.sub("", _ANSI.sub("", text))
