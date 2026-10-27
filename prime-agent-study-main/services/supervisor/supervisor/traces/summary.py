"""A run in one sentence: what it did and how it ended, from its trace alone.

"Ran 2 commands (1 failed), changed notes.txt; answered." No model is asked, so every run
gets one the moment it ends. A deployment can also ask a model for a richer line
(``supervisor.traces.judges.model_summary``); this one stays the fallback.
"""

from __future__ import annotations

from supervisor.seams.core.types import ChatMessage
from supervisor.traces.kinds import SpanKind
from supervisor.traces.model import RunStatus, RunTrace
from supervisor.traces.render import run_text

__all__ = ["SUMMARY_MODEL_ENV", "clean_summary", "describe", "summary_messages"]

_LOOKING = frozenset({"read", "ls", "grep", "glob", "find"})
_CHANGING = frozenset({"edit", "write"})


def _short(text: str, limit: int = 140) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _plural(n: int, one: str, many: str | None = None) -> str:
    return f"{n} {one if n == 1 else (many or one + 's')}"


def _paths(names: list[str], limit: int = 2) -> str:
    unique = list(dict.fromkeys(names))
    shown = ", ".join(unique[:limit])
    return shown + (f" and {len(unique) - limit} more" if len(unique) > limit else "")


def _did(trace: RunTrace) -> list[str]:
    tools = [s for s in trace.spans if s.kind is SpanKind.TOOL and not s.attrs.get("denied")]
    parts: list[str] = []
    commands = [s for s in tools if s.attrs.get("tool") == "bash"]
    if commands:
        bad = sum(1 for s in commands if s.error or s.attrs.get("exit_code") not in (None, 0))
        parts.append(f"ran {_plural(len(commands), 'command')}" + (f" ({bad} failed)" if bad else ""))
    changed = [str(s.detail.get("args", {}).get("file_path") or s.detail.get("args", {}).get("path") or "")
               for s in tools if s.attrs.get("tool") in _CHANGING and s.status.value == "ok"]
    changed = [c for c in changed if c]
    if changed:
        parts.append(f"changed {_paths(changed)}")
    looked = [s for s in tools if s.attrs.get("tool") in _LOOKING]
    if looked:
        parts.append(f"looked at {_plural(len(looked), 'thing')}")
    others = [s for s in tools if s.attrs.get("tool") not in _LOOKING | _CHANGING | {"bash"}]
    if others:
        names = list(dict.fromkeys(str(s.attrs.get("tool", s.name)) for s in others))
        parts.append("used " + ", ".join(names[:3]))
    denied = trace.rollup.denials
    if denied:
        parts.append(f"was denied {_plural(denied, 'call')}")
    return parts


def _ending(trace: RunTrace) -> str:
    r = trace.rollup
    match r.status:
        case RunStatus.OK:
            return "answered"
        case RunStatus.ERROR:
            return f"failed: {_short(r.reason)}" if r.reason else "failed"
        case RunStatus.BUDGET_EXCEEDED:
            return "ran out of budget"
        case RunStatus.CANCELLED:
            return "was stopped"
        case RunStatus.INTERRUPTED:
            return "stopped without finishing"
        case RunStatus.AWAITING_APPROVAL:
            return "is waiting for an approval"
        case _:
            return "is running"


def describe(trace: RunTrace) -> str:
    """One sentence on the run, capitalised, ending in a full stop."""
    did = _did(trace)
    ending = _ending(trace)
    text = f"{', '.join(did)}; {ending}" if did else ending
    return text[:1].upper() + text[1:] + ("" if text.endswith(".") else ".")


SUMMARY_MODEL_ENV = "TRACE_SUMMARY_MODEL"
"""Set to a model route to have a model write each finished run's summary (the rules' line
stays when it is unset, or when the model's reply is unusable)."""

SUMMARY_SYSTEM = (
    "Summarise one run of an AI agent for the person who will skim a list of runs. One "
    "sentence, at most 30 words: what was asked, what the agent did, and how it ended. "
    "Plain words, no preamble, no quotes."
)


def summary_messages(trace: RunTrace) -> list[ChatMessage]:
    return [ChatMessage(role="system", content=SUMMARY_SYSTEM), ChatMessage(role="user", content=run_text(trace))]


def clean_summary(reply: str | None) -> str | None:
    """The model's line, tidied; None when there is nothing usable."""
    line = " ".join((reply or "").split()).strip().strip('"').strip()
    if not line or len(line) < 8:
        return None
    return line if len(line) <= 240 else line[:239] + "…"
