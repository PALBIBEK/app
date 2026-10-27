"""Automatic checks on a finished run: cheap, deterministic signals of how it went.

Each check reads the run's trace and passes or fails with a reason a person can act on.
They are the first signals of the improvement loop: no model is asked, so they run on
every run. A check that does not apply to a run (no tools, no approvals) is not emitted.

Bump ``CHECKS_VERSION`` when a check's meaning changes: scores record which version
judged them, so numbers from different versions are never mixed up.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from supervisor.traces.kinds import SpanKind
from supervisor.traces.model import RunStatus, RunTrace, Span, SpanStatus

__all__ = ["CHECKS_VERSION", "REPEAT_LIMIT", "Check", "run_checks"]

CHECKS_VERSION = "checks@1"
REPEAT_LIMIT = 3
"""The same tool with the same arguments this many times in one run reads as a loop."""

_TEST_COMMAND = re.compile(
    r"\b(pytest|py\.test|unittest|tox|nox|jest|vitest|mocha|go test|cargo test|npm (run )?test|"
    r"pnpm (run )?test|yarn test|mvn test|gradle test|rspec|phpunit|make test|ctest)\b"
)


@dataclass(frozen=True)
class Check:
    name: str
    passed: bool
    reason: str
    span_id: str | None = None
    """The span the check points at (the first failure), when there is one."""
    topic: str | None = None
    """What a failure is about (a tool's name, how the run ended): failures of one check on
    the same topic group into one issue."""

    @property
    def label(self) -> str:
        return "pass" if self.passed else "fail"


def _tools(trace: RunTrace) -> list[Span]:
    return [s for s in trace.spans if s.kind is SpanKind.TOOL]


def _finished(trace: RunTrace) -> Check | None:
    r = trace.rollup
    if r.status is RunStatus.OK:
        return Check("finished", True, "answered")
    if r.status in (RunStatus.ERROR, RunStatus.BUDGET_EXCEEDED, RunStatus.INTERRUPTED):
        root = trace.spans[0] if trace.spans else None
        error = (root.error or {}).get("class") if root else None
        return Check("finished", False, r.reason or r.status.value, root.id if root else None,
                     topic=r.status.value if r.status is not RunStatus.ERROR else (error or "error"))
    return None  # stopped by a person, or still going: not the agent's doing


def _tool_errors(trace: RunTrace) -> Check | None:
    tools = [s for s in _tools(trace) if not s.attrs.get("denied")]
    if not tools:
        return None
    failed = [s for s in tools if s.status is SpanStatus.ERROR]
    if not failed:
        return Check("tool_errors", True, f"all {len(tools)} tool call{'' if len(tools) == 1 else 's'} worked")
    first = failed[0]
    why = (first.error or {}).get("message", "")
    return Check(
        "tool_errors",
        False,
        f"{len(failed)} of {len(tools)} tool calls failed; first: {first.preview or first.name}"
        + (f" ({why})" if why else ""),
        first.id,
        topic=str(first.attrs.get("tool", first.name)),
    )


def _signature(span: Span) -> str:
    args: Any = span.detail.get("args", {})
    try:
        body = json.dumps(args, sort_keys=True, default=str)
    except (TypeError, ValueError):
        body = str(args)
    return f"{span.attrs.get('tool', span.name)}:{body}"


def _repeated_calls(trace: RunTrace) -> Check | None:
    tools = [s for s in _tools(trace) if not s.attrs.get("denied")]
    if len(tools) < REPEAT_LIMIT:
        return None
    seen: dict[str, list[Span]] = {}
    for span in tools:
        seen.setdefault(_signature(span), []).append(span)
    worst = max(seen.values(), key=len)
    if len(worst) < REPEAT_LIMIT:
        return Check("repeated_calls", True, "no call repeated with the same arguments")
    return Check(
        "repeated_calls",
        False,
        f"ran {worst[0].preview or worst[0].name} {len(worst)} times with the same arguments",
        worst[REPEAT_LIMIT - 1].id,
        topic=str(worst[0].attrs.get("tool", worst[0].name)),
    )


def _approvals(trace: RunTrace) -> Check | None:
    asked = [s for s in trace.spans if s.kind is SpanKind.APPROVAL]
    if not asked:
        return None
    refused = [s for s in asked if s.attrs.get("denied")]
    if not refused:
        return Check("approvals", True, f"{len(asked)} asked, all allowed")
    timed_out = sum(1 for s in refused if s.attrs.get("outcome") == "timed_out")
    detail = f" ({timed_out} unanswered)" if timed_out else ""
    tool = refused[0].name.removeprefix("approval ").strip() or "tool"
    return Check("approvals", False, f"{len(refused)} of {len(asked)} denied{detail}", refused[0].id, topic=tool)


def _tests_after_change(trace: RunTrace) -> Check | None:
    tools = sorted(_tools(trace), key=lambda s: s.start_ms)
    changes = [s for s in tools if s.attrs.get("tool") in ("edit", "write") and s.status is SpanStatus.OK
               and not s.attrs.get("denied")]
    if not changes:
        return None
    last_change = changes[-1].start_ms
    tests = [
        s for s in tools
        if s.attrs.get("tool") == "bash" and s.start_ms > last_change and not s.attrs.get("denied")
        and _TEST_COMMAND.search(str(s.detail.get("args", {}).get("command", "")))
    ]
    if not tests:
        return None  # it did not run tests after its last change; no verdict either way
    last = tests[-1]
    code = last.attrs.get("exit_code")
    if code == 0:
        return Check("tests_after_change", True, f"{last.preview} passed after the last change", last.id)
    return Check("tests_after_change", False, f"{last.preview} exited {code} after the last change", last.id,
                 topic=str(last.detail.get("args", {}).get("command", "tests"))[:128])


def run_checks(trace: RunTrace) -> list[Check]:
    """The checks that apply to a finished run; none for a run still going."""
    if not trace.rollup.status.finished:
        return []
    found = (
        _finished(trace),
        _tool_errors(trace),
        _repeated_calls(trace),
        _approvals(trace),
        _tests_after_change(trace),
    )
    return [c for c in found if c is not None]
