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

__all__ = ["MAX_RESULT_CHARS", "MAX_RESULT_LINES", "bound", "tool_result_text"]

MAX_RESULT_CHARS: Final = 50 * 1024
MAX_RESULT_LINES: Final = 2000


def bound(text: str, *, chars: int = MAX_RESULT_CHARS, lines: int = MAX_RESULT_LINES) -> str:
    """``text`` within ``chars`` and ``lines``, keeping its head and tail, saying what was cut."""
    rows = text.split("\n")
    if len(rows) > lines:
        half = lines // 2
        cut = len(rows) - 2 * half
        text = "\n".join([*rows[:half], f"[... {cut} lines cut ...]", *rows[-half:]])
    if len(text) > chars:
        half = chars // 2
        cut = len(text) - 2 * half
        text = f"{text[:half]}\n[... {cut} characters cut ...]\n{text[-half:]}"
    return text


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
    if payload.get("timed_out"):
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
        if "exit_code" in payload and ("stdout" in payload or "stderr" in payload):
            return _command(payload)
        return _fields(payload)
    return _value(payload)


def tool_result_text(result: Mapping[str, Any] | Any) -> str:
    """The text the model reads for one journaled tool result (any shape journaled so far)."""
    if not isinstance(result, Mapping):
        return bound(_value(result))
    if isinstance(result.get("text"), str):
        return bound(result["text"])
    body = _payload(result.get("payload"))
    ok = result.get("ok")
    if ok is False or (ok is None and result.get("error")):
        code = result.get("code")
        error = str(result.get("error") or "the tool failed")
        head = f"Error{f' ({code})' if code else ''}: {error}"
        return bound(f"{head}\n{body}" if body and body != error else head)
    return bound(body or "[done]")
