"""What the model reads of a tool's result.

A tool returns structure (``{"ok", "payload", "error", "code"}``) for traces and the UI;
the model reads text. Sending it ``json.dumps`` of that structure costs it on every
later step: file contents and command output arrive on one line, with every newline
and quote escaped, and a failed command reads ``"ok": true``. githi (and pi) hand the
model what a person would read in a terminal; this does the same.

Every result is bounded: what a tool returns is re-sent on every later step of the
run, so an unbounded result is a cost on all of them, not once. The head and the tail
are kept (a command's answer and a stack trace's last line live at the end).
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any, Final

from supervisor.tools.truncate import cut_middle

__all__ = ["MAX_RESULT_CHARS", "MAX_RESULT_LINES", "bound", "tool_result_failed", "tool_result_text"]

MAX_RESULT_CHARS: Final = 50 * 1024 + 1024
MAX_RESULT_LINES: Final = 2000 + 16
"""pi's 2000 lines / 50 KB, with room for the note a tool adds below a page it cut
(read's "Use offset=…", bash's "Full output: …"): those tools bound themselves, and
this only stops a tool that does not."""


def bound(text: str, *, chars: int = MAX_RESULT_CHARS, lines: int = MAX_RESULT_LINES) -> str:
    """``text`` within ``chars`` and ``lines``, keeping its head and tail, saying what was cut."""
    rows = text.split("\n")
    if len(rows) > lines:
        half = lines // 2
        cut = len(rows) - 2 * half
        text = "\n".join([*rows[:half], f"[... {cut} lines cut ...]", *rows[-half:]])
    return cut_middle(text, chars)


def _size(size: int) -> str:
    return f"{size}B" if size < 1024 else f"{size / 1024:.1f}KB" if size < 1024 * 1024 else f"{size / 1048576:.1f}MB"


def _shell(payload: Mapping[str, Any]) -> str:
    """pi's bash result: the output's tail, where the rest is, how the command ended."""
    text = str(payload.get("output") or "")
    cut = payload.get("truncation")
    if isinstance(cut, Mapping):
        where = payload.get("full_output") or "not kept"
        if cut.get("partial"):
            note = (
                f"[Showing last {_size(int(cut.get('shown_bytes') or 0))} of line {cut.get('end')}"
                f" (line is {_size(int(cut.get('line_bytes') or 0))}). Full output: {where}]"
            )
        elif cut.get("by") == "lines":
            note = f"[Showing lines {cut.get('start')}-{cut.get('end')} of {cut.get('total')}. Full output: {where}]"
        else:
            note = f"[Showing lines {cut.get('start')}-{cut.get('end')} of {cut.get('total')} (50.0KB limit). Full output: {where}]"
        text = f"{text}\n\n{note}"
    if payload.get("stopped"):
        status = "Command stopped: the run was stopped while it ran, and the command was killed"
        return f"{text}\n\n{status}" if text else status
    if payload.get("timed_out"):
        status = f"Command timed out after {float(payload.get('timeout_s') or 0):g} seconds"
        return f"{text}\n\n{status}" if text else status
    text = text or "(no output)"
    code = payload.get("exit_code")
    if code not in (None, 0):
        return f"{text}\n\nCommand exited with code {code}"
    return text


_DONE: Final[dict[str, Any]] = {
    # What pi's tools tell the model on success (the payload keeps the rest for people).
    "write": lambda p: f"Successfully wrote to {p.get('path')}",
    "edit": lambda p: f"Successfully replaced {p.get('replacements')} block(s) in {p.get('path')}.",
}


def _command(payload: Mapping[str, Any]) -> str:
    """A shell command's result as a terminal shows it, its failure stated plainly."""
    parts: list[str] = []
    stdout = str(payload.get("stdout") or "").rstrip()
    stderr = str(payload.get("stderr") or "").rstrip()
    if stdout:
        parts.append(stdout)
    if stderr:
        parts.append(f"[stderr]\n{stderr}")
    if payload.get("notice"):
        parts.append(f"[{payload['notice']}]")
    code = payload.get("exit_code")
    if payload.get("stopped"):
        parts.append("[the run was stopped while the command ran; it was killed]")
    elif payload.get("timed_out"):
        parts.append("[the command timed out and was killed]")
    elif code not in (None, 0):
        parts.append(f"[exit code {code}: the command failed]")
    elif not parts:
        # Nothing to read invites running it again to find out what happened.
        parts.append("[no output; exit code 0]")
    return "\n".join(parts)


def _value(value: Any) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, default=str)


def _fields(payload: Mapping[str, Any]) -> str:
    """Other structured results, one field per line; long text keeps its own lines."""
    lines: list[str] = []
    for key, value in payload.items():
        if isinstance(value, str) and "\n" in value:
            lines.append(f"{key}:\n{value.rstrip()}")
        else:
            lines.append(f"{key}: {_value(value)}")
    return "\n".join(lines)


def _payload(payload: Any) -> str:
    if payload is None:
        return ""
    if isinstance(payload, str):
        return payload
    if isinstance(payload, Mapping):
        if "exit_code" in payload and "output" in payload:
            return _shell(payload)
        if "exit_code" in payload and ("stdout" in payload or "stderr" in payload):
            return _command(payload)
        return _fields(payload)
    return _value(payload)


def tool_result_failed(result: Mapping[str, Any] | Any) -> bool:
    """Whether the model is told this result failed: the tool failed, or (pi's bash)
    the command exited non-zero or timed out."""
    if not isinstance(result, Mapping):
        return False
    ok = result.get("ok")
    if ok is False or (ok is None and result.get("error")):
        return True
    payload = result.get("payload")
    return (
        isinstance(payload, Mapping)
        and "output" in payload
        and (
            bool(payload.get("timed_out"))
            or bool(payload.get("stopped"))
            or payload.get("exit_code") not in (None, 0)
        )
    )


def tool_result_text(result: Mapping[str, Any] | Any, tool: str | None = None) -> str:
    """The text the model reads for one journaled tool result (any shape journaled so far).

    A failure is its message, as pi's results read: the error code stays in the
    journal, and the provider is told the result failed (``tool_result_failed``).
    """
    if not isinstance(result, Mapping):
        return bound(_value(result))
    if isinstance(result.get("text"), str):
        return bound(result["text"])
    payload = result.get("payload")
    ok = result.get("ok")
    if ok is False or (ok is None and result.get("error")):
        error = str(result.get("error") or "the tool failed")
        body = _payload(payload)
        return bound(f"{error}\n{body}" if body and body != error else error)
    if tool in _DONE and isinstance(payload, Mapping) and payload.get("path") is not None:
        return bound(_DONE[tool](payload))
    return bound(_payload(payload) or "[done]")
