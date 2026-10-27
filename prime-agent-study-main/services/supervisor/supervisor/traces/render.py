"""A run as plain text: what a judge (or a summarizing model) reads.

The same order a person reads in the transcript: the ask, what the agent said, each tool
call with its arguments and the start of its result, approvals, and how it ended. Long
parts are cut so a whole run fits a judge's prompt; the cut is marked, never silent.
"""

from __future__ import annotations

import json
from typing import Any

from supervisor.traces.kinds import SpanKind
from supervisor.traces.model import RunTrace, Span

__all__ = ["PART_LIMIT", "RUN_TEXT_LIMIT", "run_text"]

PART_LIMIT = 1_500
"""Characters of one message, argument list or result."""
RUN_TEXT_LIMIT = 24_000
"""Characters of the whole run; beyond it the middle is cut (the start and the end matter most)."""


def _cut(text: str, limit: int = PART_LIMIT) -> str:
    text = text.strip()
    return text if len(text) <= limit else text[:limit] + f" … [{len(text) - limit} more characters]"


def _args(span: Span) -> str:
    args: Any = span.detail.get("args", {})
    try:
        return _cut(json.dumps(args, ensure_ascii=False, sort_keys=True, default=str))
    except (TypeError, ValueError):
        return _cut(str(args))


def _lines(trace: RunTrace) -> list[str]:
    out: list[str] = []
    root = trace.spans[0] if trace.spans else None
    if root is not None and (ask := root.detail.get("input") or trace.rollup.input):
        out.append(f"USER: {_cut(str(ask))}")
    for span in trace.spans:
        if span.kind is SpanKind.LLM and (text := span.detail.get("text")):
            out.append(f"AGENT: {_cut(str(text))}")
        elif span.kind is SpanKind.TOOL:
            line = f"TOOL CALL {span.attrs.get('tool', span.name)} {_args(span)}"
            if span.attrs.get("denied"):
                out.append(f"{line}\n  NOT RUN: {span.preview}")
                continue
            result = span.excerpt or ""
            status = "FAILED" if span.error else "RESULT"
            exit_code = span.attrs.get("exit_code")
            head = f"{status}{f' (exit {exit_code})' if exit_code is not None else ''}"
            out.append(f"{line}\n  {head}: {_cut(result) if result else (span.error or {}).get('message', '')}")
        elif span.kind is SpanKind.APPROVAL:
            out.append(f"APPROVAL: {span.preview}")
        elif span.kind is SpanKind.AGENT and span.parent_id is not None:
            out.append(f"SUBAGENT {span.name}: {_cut(span.excerpt or span.preview or '')}")
    r = trace.rollup
    out.append(f"ENDED: {r.status.value}{f' ({r.reason})' if r.reason else ''}")
    return out


def run_text(trace: RunTrace, *, limit: int = RUN_TEXT_LIMIT) -> str:
    text = "\n".join(_lines(trace))
    if len(text) <= limit:
        return text
    keep = limit // 2
    return f"{text[:keep]}\n… [{len(text) - limit} characters of the middle of the run left out] …\n{text[-keep:]}"
