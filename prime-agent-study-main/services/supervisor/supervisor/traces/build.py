"""Build a run's trace from its journal events: a pure function, the same tree every time.

The journal says what happened in order; the trace says how it nests and how long
each part took. A run is the root; each pass of the loop is a step; a step holds
its model call and the tools that call asked for; an approval sits under the tool
it gates. Span ids hash the session, run and a key that names the span (a tool's
call id, a step's number), so rebuilding from the same journal gives the same ids.

What this reads is everything ``agent.events`` journals for one run (``turn_id``).
Unknown event types are ignored, so an older builder reads a newer journal.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Sequence
from typing import Any

from supervisor.agent.events import (
    ApprovalRequired,
    ApprovalResolved,
    BackgroundResults,
    CompactionEnd,
    CompactionStart,
    Envelope,
    ErrorEvent,
    HandCheckpointed,
    HandLost,
    HandProvisioned,
    HandStopped,
    JobEnd,
    JobStart,
    MemoryInjected,
    MemoryRecall,
    MessageEnd,
    RunEnd,
    RunStart,
    SubagentEnd,
    SubagentStart,
    ToolEnd,
    ToolStart,
    TurnEnd,
    TurnStart,
)
from supervisor.policy.types import is_allow_choice
from supervisor.traces.kinds import SpanKind
from supervisor.traces.model import (
    Marker,
    Rollup,
    RunStatus,
    RunTrace,
    Span,
    SpanStatus,
    TimeSplit,
)

__all__ = ["DETAIL_LIMIT", "EXCERPT_LIMIT", "PREVIEW_LIMIT", "build_run_trace", "span_id", "trace_id"]

PREVIEW_LIMIT = 160
"""Characters in a one-line preview."""
EXCERPT_LIMIT = 2_000
"""Characters of a span's excerpt: enough to read inline, small enough for every span of a tree."""
DETAIL_LIMIT = 64_000
"""Characters kept of one text in a span's detail; the rest is cut and its length noted."""

_DENIED_CODES = frozenset({"APPROVAL_DENIED", "APPROVAL_TIMEOUT", "APPROVAL_TIMED_OUT"})
_NOT_ALLOWED = frozenset({"denied", "timed_out", "cancelled"})
"""``ApprovalResolved.status`` values under which the call did not run, whatever the choice."""
_FAILED_SUBAGENT = frozenset({"error", "timeout", "failed"})
_CANCELLED = frozenset({"aborted", "cancelled"})


def trace_id(session_id: str, run_id: str) -> str:
    return hashlib.sha256(f"{session_id}:{run_id}".encode()).hexdigest()[:32]


def span_id(session_id: str, run_id: str, key: str) -> str:
    return hashlib.sha256(f"{session_id}:{run_id}:{key}".encode()).hexdigest()[:16]


# --- text helpers -------------------------------------------------------------


def _line(text: str | None, limit: int = PREVIEW_LIMIT) -> str | None:
    """The first non-empty line of ``text``, cut to ``limit``."""
    if not text:
        return None
    for raw in text.splitlines():
        if line := raw.strip():
            return line if len(line) <= limit else line[: limit - 1] + "…"
    return None


def _cut(value: Any) -> Any:
    """``value`` with any string longer than ``DETAIL_LIMIT`` shortened (recursively)."""
    if isinstance(value, str):
        if len(value) <= DETAIL_LIMIT:
            return value
        return value[:DETAIL_LIMIT] + f"\n… [{len(value) - DETAIL_LIMIT} more characters]"
    if isinstance(value, dict):
        return {k: _cut(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_cut(v) for v in value]
    return value


def _excerpt(text: str | None) -> str | None:
    if not text:
        return None
    return text if len(text) <= EXCERPT_LIMIT else text[:EXCERPT_LIMIT] + "…"


def _result_text(result: dict[str, Any]) -> str | None:
    """What a tool's result says, as text: an error, a command's output, a file's text."""
    if not result.get("ok", True):
        return str(result.get("error") or "failed")
    payload = result.get("payload")
    if isinstance(payload, dict) and ("stdout" in payload or "stderr" in payload):
        out, err = str(payload.get("stdout") or ""), str(payload.get("stderr") or "")
        return (out + ("\n" if out and err and not out.endswith("\n") else "") + err) or None
    if isinstance(payload, str):
        return payload
    return _compact_json(payload) if payload is not None else None


def _text(content: Iterable[Any]) -> str:
    return "".join(
        str(b.get("text", "")) for b in content if isinstance(b, dict) and b.get("type") == "text"
    )


def _tool_calls(content: Iterable[Any]) -> list[dict[str, Any]]:
    return [
        {"id": b.get("id"), "name": b.get("name"), "arguments": b.get("arguments") or b.get("input") or {}}
        for b in content
        if isinstance(b, dict) and b.get("type") in ("tool_call", "tool_use")
    ]


def _compact_json(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, separators=(", ", ": "), default=str)
    except (TypeError, ValueError):
        return str(value)


def _seconds(ms: int) -> str:
    s = ms / 1000
    if s < 60:
        return f"{s:.0f}s" if s >= 10 else f"{s:.1f}s"
    return f"{s / 60:.0f} min"


# --- what one tool call reads as ---------------------------------------------


def _path(args: dict[str, Any]) -> str | None:
    p = args.get("file_path") or args.get("path")
    return str(p) if p else None


def _tool_preview(name: str, args: dict[str, Any]) -> str:
    """What the call asks for, in a line: ``$ pytest -q``, ``edit app.py``."""
    if name == "bash" and args.get("command"):
        return _line(f"$ {args['command']}") or name
    if (path := _path(args)) is not None:
        return f"{name} {path}"
    if name == "grep" and args.get("pattern"):
        return _line(f"grep {args['pattern']}") or name
    return (_line(f"{name} {_compact_json(args)}") or name) if args else name


def _change(name: str, args: dict[str, Any]) -> dict[str, Any] | None:
    """For a call that changes a file: the path and the text removed and added."""
    path = _path(args)
    if name == "edit" and path:
        return {"path": path, "removed": args.get("old_text", ""), "added": args.get("new_text", ""),
                "replace_all": bool(args.get("replace_all"))}
    if name == "write" and path:
        return {"path": path, "removed": None, "added": args.get("content", "")}
    return None


def _result_preview(name: str, result: dict[str, Any]) -> str | None:
    if not result.get("ok", True):
        return _line(str(result.get("error") or "failed"))
    payload = result.get("payload")
    if isinstance(payload, dict) and "exit_code" in payload:
        out = _line(str(payload.get("stdout") or payload.get("stderr") or ""), PREVIEW_LIMIT - 12)
        return f"exit {payload['exit_code']}" + (f" · {out}" if out else "")
    if isinstance(payload, str):
        n = payload.count("\n") + (0 if payload.endswith("\n") else 1) if payload else 0
        return f"{n} line{'' if n == 1 else 's'}" if name in ("read", "ls", "grep") else _line(payload)
    if isinstance(payload, dict) and "bytes" in payload:
        return f"{payload['bytes']} bytes written"
    return _line(_compact_json(payload)) if payload is not None else None


# --- interval arithmetic for the time split ----------------------------------


def _union(intervals: Iterable[tuple[int, int]]) -> list[tuple[int, int]]:
    out: list[tuple[int, int]] = []
    for a, b in sorted(i for i in intervals if i[1] > i[0]):
        if out and a <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], b))
        else:
            out.append((a, b))
    return out


def _minus(xs: list[tuple[int, int]], ys: list[tuple[int, int]]) -> list[tuple[int, int]]:
    out: list[tuple[int, int]] = []
    for a, b in xs:
        cur = a
        for c, d in ys:
            if d <= cur or c >= b:
                continue
            if c > cur:
                out.append((cur, c))
            cur = max(cur, d)
        if cur < b:
            out.append((cur, b))
    return out


def _total(xs: Iterable[tuple[int, int]]) -> int:
    return sum(b - a for a, b in xs)


# --- the builder ---------------------------------------------------------------


class _Builder:
    def __init__(self, session_id: str, run_id: str, agent: str | None) -> None:
        self.session_id, self.run_id, self.agent = session_id, run_id, agent
        self.spans: list[Span] = []
        self.root: Span | None = None
        self.step: Span | None = None
        self.steps = 0
        self.tools: dict[str, Span] = {}
        self.approvals: dict[str, Span] = {}
        self.opened: dict[str, Span] = {}
        """Compactions, subagents and jobs still open, by their key."""
        self.asked: dict[str, dict[str, Any]] = {}
        """Tool calls the model asked for, by call id (their arguments, before the tool starts)."""
        self.prev_ts: int | None = None
        self.last_ts = 0
        self.stop_reason: str | None = None
        self.ended = False
        self.errors: list[dict[str, str]] = []
        self.error_targets: set[str] = set()
        """Spans an ErrorEvent marked (already counted in ``errors``)."""
        self.output: str | None = None

    # spans

    def _id(self, key: str) -> str:
        return span_id(self.session_id, self.run_id, key)

    def _open(self, key: str, kind: SpanKind, name: str, env: Envelope, parent: Span | None) -> Span:
        span = Span(id=self._id(key), parent_id=parent.id if parent else None, kind=kind, name=name,
                    start_ms=env.ts_ms, seqs=[env.seq])
        self.spans.append(span)
        return span

    def _close(self, span: Span, env: Envelope, status: SpanStatus = SpanStatus.OK) -> None:
        span.end_ms = env.ts_ms
        span.seqs.append(env.seq)
        if span.status is not SpanStatus.ERROR:
            span.status = status

    def _parent(self) -> Span | None:
        return self.step or self.root

    def _ensure_root(self, env: Envelope) -> Span:
        if self.root is None:
            self.root = self._open("run", SpanKind.AGENT, self.agent or "run", env, None)
        return self.root

    # events

    def add(self, env: Envelope) -> None:
        p = env.payload
        self._ensure_root(env)
        handler = getattr(self, f"_on_{type(p).__name__}", None)
        if handler is not None:
            handler(env, p)
        self.prev_ts = env.ts_ms
        self.last_ts = max(self.last_ts, env.ts_ms)

    def _on_RunStart(self, env: Envelope, p: RunStart) -> None:
        assert self.root is not None
        self.root.start_ms = min(self.root.start_ms, env.ts_ms)

    def _on_MessageEnd(self, env: Envelope, p: MessageEnd) -> None:
        root = self.root
        assert root is not None
        if p.role == "user":
            text = _text(p.content)
            root.detail.setdefault("input", _cut(text))
            root.preview = root.preview or _line(text)
            root.excerpt = root.excerpt or _excerpt(text)
            root.seqs.append(env.seq)
            return
        if p.role != "assistant":
            return
        text = _text(p.content)
        calls = _tool_calls(p.content)
        for call in calls:
            if call["id"]:
                self.asked[str(call["id"])] = call
        start = self.prev_ts if self.prev_ts is not None else env.ts_ms
        if self.step is not None:
            start = max(start, self.step.start_ms)
        span = self._open(f"llm:{env.seq}", SpanKind.LLM, f"chat {p.model}" if p.model else "model call",
                          env, self._parent())
        span.start_ms = min(start, env.ts_ms)
        attrs: dict[str, Any] = {"model": p.model, "finish_reason": p.finish_reason}
        if p.usage is not None:
            attrs.update(input_tokens=p.usage.input_tokens, output_tokens=p.usage.output_tokens,
                         cache_read_tokens=p.usage.cache_read, cache_write_tokens=p.usage.cache_write,
                         cost=p.usage.cost_total)
        if "cost" in attrs:
            attrs["cost"] = round(float(attrs["cost"]), 6)
        span.attrs = {k: v for k, v in attrs.items() if v is not None}
        span.detail = {"text": _cut(text), "tool_calls": _cut(calls)}
        span.excerpt = _excerpt(text)
        names = ", ".join(str(c["name"]) for c in calls)
        span.preview = _line(text) or (f"asks for {names}" if names else None)
        self._close(span, env)
        if text:
            self.output = text

    def _on_TurnStart(self, env: Envelope, p: TurnStart) -> None:
        if self.step is not None:
            self._close(self.step, env)
        self.steps += 1
        self.step = self._open(f"step:{self.steps}", SpanKind.STEP, f"step {self.steps}", env, self.root)
        self.step.attrs["index"] = self.steps

    def _on_TurnEnd(self, env: Envelope, p: TurnEnd) -> None:
        self.stop_reason = p.stop_reason
        if self.step is not None:
            self.step.attrs["stop_reason"] = p.stop_reason
            self._close(self.step, env)
            self.step = None

    def _tool_span(self, call_id: str, name: str, env: Envelope) -> Span:
        span = self.tools.get(call_id)
        if span is None:
            span = self._open(f"tool:{call_id}", SpanKind.TOOL, name, env, self._parent())
            span.attrs.update(tool=name, call_id=call_id)
            args = dict(self.asked.get(call_id, {}).get("arguments") or {})
            span.detail["args"] = _cut(args)
            span.preview = _tool_preview(name, args)
            self.tools[call_id] = span
        return span

    def _on_ApprovalRequired(self, env: Envelope, p: ApprovalRequired) -> None:
        meta = p.metadata or {}
        call_id, tool = meta.get("tool_call_id"), meta.get("tool")
        parent = self._tool_span(str(call_id), str(tool or "tool"), env) if call_id else self._parent()
        span = self._open(f"approval:{p.approval_id}", SpanKind.APPROVAL,
                          f"approval {tool}" if tool else "approval", env, parent)
        span.attrs.update(timeout_s=p.timeout_s)
        span.detail = {"prompt": p.prompt, "options": list(p.options)}
        span.preview = "waiting for a person"
        self.approvals[p.approval_id] = span

    def _on_ApprovalResolved(self, env: Envelope, p: ApprovalResolved) -> None:
        span = self.approvals.pop(p.approval_id, None)
        if span is None:
            return
        self._close(span, env)
        wait = env.ts_ms - span.start_ms
        denied = p.status in _NOT_ALLOWED or not is_allow_choice(p.choice)
        span.attrs.update(decision=p.choice, outcome=p.status, responded_by=p.responded_by,
                          wait_ms=wait, denied=denied)
        if p.status == "timed_out":
            span.preview = f"no answer in {_seconds(wait)}, so {p.choice}"
        elif p.status == "cancelled":
            span.preview = "cancelled"
        else:  # who answered is a principal id: the product names it (``responded_by``)
            span.preview = f"{'denied' if denied else 'allowed'} after {_seconds(wait)}"

    def _on_ToolStart(self, env: Envelope, p: ToolStart) -> None:
        span = self._tool_span(p.tool_call_id, p.tool_name, env)
        span.seqs.append(env.seq)
        args = dict(p.args or {})
        span.detail["args"] = _cut(args)
        span.preview = _tool_preview(p.tool_name, args)
        span.attrs["exec_start_ms"] = env.ts_ms
        if change := _change(p.tool_name, args):
            span.detail["change"] = _cut(change)

    def _on_ToolEnd(self, env: Envelope, p: ToolEnd) -> None:
        span = self._tool_span(p.tool_call_id, p.tool_name, env)  # an end with no start still shows
        del self.tools[p.tool_call_id]
        result = dict(p.result or {})
        span.detail["result"] = _cut(result)
        span.excerpt = _excerpt(_result_text(result))
        start = int(span.attrs.get("exec_start_ms", env.ts_ms))
        span.attrs["exec_ms"] = p.duration_ms if p.duration_ms is not None else env.ts_ms - start
        payload = result.get("payload")
        if isinstance(payload, dict) and "exit_code" in payload:
            span.attrs["exit_code"] = payload["exit_code"]
        code = result.get("code")
        if code in _DENIED_CODES:
            span.attrs["denied"] = True
            span.attrs.pop("exec_start_ms", None)
            span.attrs["exec_ms"] = 0
            span.preview = f"not run: {_line(str(result.get('error') or 'denied'))}"
            self._close(span, env)
            return
        outcome = _result_preview(p.tool_name, result)
        if outcome:
            span.attrs["outcome"] = outcome
        if p.is_error:
            span.error = {"class": str(code or "ToolError"), "message": str(result.get("error") or "failed")}
            self._close(span, env, SpanStatus.ERROR)
        else:
            self._close(span, env)

    def _on_ErrorEvent(self, env: Envelope, p: ErrorEvent) -> None:
        error = {"class": p.error_class, "message": p.detail}
        if p.error_code:
            error["code"] = p.error_code
        self.errors.append(error)
        target = next(reversed(self.tools.values()), None) or self.step or self.root
        assert target is not None
        target.status = SpanStatus.ERROR
        target.error = error
        target.seqs.append(env.seq)
        self.error_targets.add(target.id)

    def _marker(self, env: Envelope, name: str, text: str) -> None:
        target = self._parent()
        assert target is not None
        target.markers.append(Marker(ts_ms=env.ts_ms, name=name, text=text, seq=env.seq))

    def _on_HandProvisioned(self, env: Envelope, p: HandProvisioned) -> None:
        cause = p.cause.replace("_", " ")
        self._marker(env, "sandbox_started", f"{p.hand} sandbox started on {p.provider} ({cause})")

    def _on_HandCheckpointed(self, env: Envelope, p: HandCheckpointed) -> None:
        self._marker(env, "sandbox_checkpointed",
                     f"{p.hand} sandbox saved: {p.files} file{'' if p.files == 1 else 's'} changed"
                     + (f", {p.removed} removed" if p.removed else ""))

    def _on_HandStopped(self, env: Envelope, p: HandStopped) -> None:
        self._marker(env, "sandbox_stopped", f"{p.hand} sandbox stopped: {p.reason.replace('_', ' ')}")

    def _on_HandLost(self, env: Envelope, p: HandLost) -> None:
        self._marker(env, "sandbox_lost", f"{p.hand} sandbox lost: {p.reason.replace('_', ' ')}")

    def _on_BackgroundResults(self, env: Envelope, p: BackgroundResults) -> None:
        n = len(p.results)
        self._marker(env, "background_results", f"{n} background result{'' if n == 1 else 's'} delivered")

    def _memory(self, env: Envelope, key: str, preview: str, attrs: dict[str, Any], detail: dict[str, Any]) -> None:
        span = self._open(key, SpanKind.MEMORY, "recall", env, self._parent())
        span.attrs, span.detail, span.preview = attrs, _cut(detail), preview
        self._close(span, env)

    def _on_MemoryInjected(self, env: Envelope, p: MemoryInjected) -> None:
        if not p.text and not p.shown:
            return  # nothing was recalled: not worth a row
        n = len(p.shown)
        self._memory(env, f"memory:{env.seq}", f"{n} fact{'' if n == 1 else 's'} recalled" if n else "context recalled",
                     {"items": n}, {"text": p.text, "shown": p.shown})

    def _on_MemoryRecall(self, env: Envelope, p: MemoryRecall) -> None:
        self._memory(env, f"memory:{env.seq}", f"{p.source} recall · {p.token_estimate} tokens",
                     {"source": p.source, "tokens": p.token_estimate, "injected": p.injected,
                      "score": p.relevance_score},
                     {"text": p.text, "block_id": p.block_id})

    def _on_CompactionStart(self, env: Envelope, p: CompactionStart) -> None:
        span = self._open(f"compaction:{p.compaction_id}", SpanKind.COMPACTION, "compact", env, self._parent())
        span.attrs.update(reason=p.reason, tokens_before=p.tokens_before)
        span.preview = f"{p.reason}: {p.tokens_before} tokens"
        self.opened[f"compaction:{p.compaction_id}"] = span

    def _on_CompactionEnd(self, env: Envelope, p: CompactionEnd) -> None:
        span = self.opened.pop(f"compaction:{p.compaction_id}", None)
        if span is None:
            return
        span.attrs.update(stop_reason=p.stop_reason, tokens_after=p.tokens_after)
        if p.tokens_after is not None:
            span.preview = f"{span.attrs.get('tokens_before')} → {p.tokens_after} tokens"
        failed = p.stop_reason == "error"
        if failed:
            span.error = {"class": "CompactionFailed", "message": "compaction did not complete"}
        self._close(span, env, SpanStatus.ERROR if failed else SpanStatus.OK)

    def _on_SubagentStart(self, env: Envelope, p: SubagentStart) -> None:
        span = self._open(f"subagent:{p.run_id}", SpanKind.AGENT, p.label or p.provider, env, self._parent())
        span.attrs.update(mode=p.mode, provider=p.provider, child_session_id=p.child_session_id,
                          subagent_run_id=p.run_id)
        span.preview = f"{p.mode.replace('_', ' ')} subagent"
        self.opened[f"subagent:{p.run_id}"] = span

    def _on_SubagentEnd(self, env: Envelope, p: SubagentEnd) -> None:
        span = self.opened.pop(f"subagent:{p.run_id}", None)
        if span is None:
            return
        span.attrs["stop_reason"] = p.stop_reason
        span.detail["output"] = _cut(p.output)
        span.excerpt = _excerpt(p.output)
        span.preview = _line(p.output) or span.preview
        failed = p.stop_reason in _FAILED_SUBAGENT
        if failed:
            span.error = {"class": "SubagentFailed", "message": f"ended with {p.stop_reason}"}
        self._close(span, env, SpanStatus.ERROR if failed else SpanStatus.OK)

    def _on_JobStart(self, env: Envelope, p: JobStart) -> None:
        span = self._open(f"job:{p.job_id}", SpanKind.JOB, f"job {p.kind}", env, self.root)
        span.attrs["job_kind"] = p.kind
        span.detail["command"] = p.command
        span.preview = _line(p.command) or p.kind
        self.opened[f"job:{p.job_id}"] = span

    def _on_JobEnd(self, env: Envelope, p: JobEnd) -> None:
        span = self.opened.pop(f"job:{p.job_id}", None)
        if span is None:
            return
        span.attrs.update(stop_reason=p.stop_reason, exit_code=p.exit_code)
        span.detail["output"] = _cut(p.output)
        span.excerpt = _excerpt(p.output)
        failed = p.exit_code not in (None, 0) or p.stop_reason in _FAILED_SUBAGENT
        if failed:
            span.error = {"class": "JobFailed", "message": f"exit {p.exit_code}" if p.exit_code else p.stop_reason}
        self._close(span, env, SpanStatus.ERROR if failed else SpanStatus.OK)

    def _on_RunEnd(self, env: Envelope, p: RunEnd) -> None:
        self.ended = True
        self._finish_open(env.ts_ms, SpanStatus.UNFINISHED)
        assert self.root is not None
        self._close(self.root, env)
        if p.total_usage is not None:
            self.root.attrs["reported_cost"] = p.total_usage.cost_total

    # finishing

    def _finish_open(self, end_ms: int, status: SpanStatus) -> None:
        for span in self.spans:
            if span is not self.root and span.end_ms is None:
                if status is SpanStatus.UNFINISHED:
                    span.end_ms = end_ms
                if span.status is SpanStatus.RUNNING:
                    span.status = status
        self.tools.clear()
        self.approvals.clear()
        self.opened.clear()
        self.step = None

    def finish(self, *, session_running: bool) -> RunTrace:
        root = self.root
        assert root is not None
        awaiting = bool(self.approvals)
        if not self.ended and not session_running:
            self._finish_open(self.last_ts, SpanStatus.UNFINISHED)
            root.end_ms = self.last_ts
            root.status = SpanStatus.UNFINISHED if root.status is not SpanStatus.ERROR else root.status
        _mark_errors_below(self.spans)
        status, reason = self._status(awaiting=awaiting, session_running=session_running)
        if status in (RunStatus.ERROR, RunStatus.BUDGET_EXCEEDED):
            root.status = SpanStatus.ERROR
            root.error = root.error or (self.errors[-1] if self.errors else {"class": status.value, "message": reason or ""})
        root.attrs.update(status=status.value, stop_reason=self.stop_reason)
        root.detail["output"] = _cut(self.output)
        if self.errors:
            root.detail["errors"] = self.errors
        rollup = self._rollup(status, reason)
        self.spans.sort(key=lambda s: (s.start_ms, s.seqs[0] if s.seqs else 0))
        return RunTrace(self.session_id, self.run_id, trace_id(self.session_id, self.run_id), self.spans, rollup)

    def _status(self, *, awaiting: bool, session_running: bool) -> tuple[RunStatus, str | None]:
        last_error = self.errors[-1] if self.errors else None
        if not self.ended:
            if not session_running:
                return RunStatus.INTERRUPTED, "the run stopped without finishing"
            return (RunStatus.AWAITING_APPROVAL, "waiting for an approval") if awaiting else (RunStatus.RUNNING, None)
        budget = next((e for e in self.errors if e["class"] == "BudgetExceeded"), None)
        if self.stop_reason == "budget_exceeded" or budget is not None:
            return RunStatus.BUDGET_EXCEEDED, (budget or {}).get("message") or "the session's budget ran out"
        if self.stop_reason in _CANCELLED:
            return RunStatus.CANCELLED, "stopped before it finished"
        if last_error is not None:
            return RunStatus.ERROR, f"{last_error['class']}: {last_error['message']}"
        if self.stop_reason in ("error", "timeout"):
            return RunStatus.ERROR, f"ended with {self.stop_reason}"
        return RunStatus.OK, None

    def _rollup(self, status: RunStatus, reason: str | None) -> Rollup:
        root = self.root
        assert root is not None
        llm = [s for s in self.spans if s.kind is SpanKind.LLM]
        tools = [s for s in self.spans if s.kind is SpanKind.TOOL]
        approvals = [s for s in self.spans if s.kind is SpanKind.APPROVAL]
        end = root.end_ms
        horizon = end if end is not None else self.last_ts
        human = _union((s.start_ms, s.end_ms if s.end_ms is not None else horizon) for s in approvals)
        execs = _union(
            (int(s.attrs["exec_start_ms"]), s.end_ms if s.end_ms is not None else horizon)
            for s in tools if "exec_start_ms" in s.attrs
        )
        model = _union((s.start_ms, s.end_ms or s.start_ms) for s in llm)
        tool_only = _minus(execs, human)
        model_only = _minus(_minus(model, human), execs)
        wall = max(0, horizon - root.start_ms)
        covered = _total(_union([*human, *execs, *model]))
        time = TimeSplit(model_ms=_total(model_only), tool_ms=_total(tool_only), human_ms=_total(human),
                         other_ms=max(0, wall - covered))
        names: dict[str, int] = {}
        for s in tools:
            names[str(s.attrs.get("tool", s.name))] = names.get(str(s.attrs.get("tool", s.name)), 0) + 1
        timed = [s for s in [*llm, *tools] if s.duration_ms is not None]
        slowest = max(timed, key=lambda s: int(s.attrs.get("exec_ms", s.duration_ms) or 0), default=None)
        priced = [s for s in llm if s.attrs.get("cost")]
        costliest = max(priced, key=lambda s: float(s.attrs["cost"]), default=None)
        return Rollup(
            status=status,
            reason=reason,
            started_ms=root.start_ms,
            ended_ms=end,
            duration_ms=wall if end is not None else None,
            time=time,
            input=_line(root.detail.get("input"), 500),
            output=_line(self.output, 500),
            cost=round(sum(float(s.attrs.get("cost", 0) or 0) for s in llm), 6),
            input_tokens=sum(int(s.attrs.get("input_tokens", 0) or 0) for s in llm),
            output_tokens=sum(int(s.attrs.get("output_tokens", 0) or 0) for s in llm),
            cache_read_tokens=sum(int(s.attrs.get("cache_read_tokens", 0) or 0) for s in llm),
            steps=self.steps,
            llm_calls=len(llm),
            tool_calls=len(tools),
            tool_errors=sum(1 for s in tools if s.status is SpanStatus.ERROR),
            approvals=len(approvals),
            denials=sum(1 for s in approvals if s.attrs.get("denied")),
            errors=len(self.errors) + sum(
                1 for s in self.spans
                if s.status is SpanStatus.ERROR and s is not root and s.id not in self.error_targets
            ),
            models=sorted({str(s.attrs["model"]) for s in llm if s.attrs.get("model")}),
            tools=names,
            slowest_span_id=slowest.id if slowest else None,
            costliest_span_id=costliest.id if costliest else None,
        )


def _mark_errors_below(spans: Sequence[Span]) -> None:
    by_id = {s.id: s for s in spans}
    for span in spans:
        if span.status is not SpanStatus.ERROR:
            continue
        parent = by_id.get(span.parent_id or "")
        while parent is not None and not parent.error_below:
            parent.error_below = True
            parent = by_id.get(parent.parent_id or "")


def build_run_trace(
    session_id: str,
    run_id: str,
    events: Iterable[Envelope],
    *,
    agent: str | None = None,
    session_running: bool = True,
) -> RunTrace | None:
    """The trace of run ``run_id`` from its journal events (in seq order); None if it has none.

    ``session_running`` says whether the session could still be working on the run: when
    it cannot, a run with no end is ``interrupted`` and its open spans ``unfinished``.
    """
    builder = _Builder(session_id, run_id, agent)
    seen = False
    for env in events:
        if env.turn_id not in (run_id, None):
            continue  # another run's event; session events (no run) inside its range belong to it
        builder.add(env)
        seen = True
    return builder.finish(session_running=session_running) if seen else None
