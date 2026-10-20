"""Journal → span projector (Phase1 Track A).

Every span derives from a committed journal Envelope. No sandbox spans.
Exporter failures are swallowed — never raise into the agent path.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Protocol
from uuid import uuid4

from supervisor.agent.events import (
    ApprovalRequired,
    ApprovalResolved,
    CompactionEnd,
    CompactionStart,
    Envelope,
    ErrorEvent,
    JobEnd,
    JobStart,
    MemoryRecall,
    MessageEnd,
    RunEnd,
    RunStart,
    Settled,
    SubagentEnd,
    SubagentStart,
    ToolEnd,
    ToolStart,
    TurnEnd,
    TurnStart,
)
from supervisor.telemetry_contract.operations import GenAIOperation, PlatformOperation
from supervisor.telemetry_contract.run_id import resolve_run_id
from supervisor.telemetry_contract.spans import SpanRecord, SpanStatus
from supervisor.telemetry_contract.usage import to_genai_tokens, to_yal_cost

logger = logging.getLogger(__name__)

# Every AgentEvent → how it is projected (Phase1 §3.2). Values are documentation keys.
PROJECTION_TABLE: dict[str, str] = {
    "RunStart": "root_span",
    "RunEnd": "close_root",
    "TurnStart": "platform_span",
    "TurnEnd": "close_turn",
    "Settled": "event_on_turn",
    "MessageEnd": "chat_span",
    "ToolStart": "tool_span",
    "ToolEnd": "close_tool",
    "ErrorEvent": "status_on_enclosing",
    "ApprovalRequired": "platform_span",
    "ApprovalResolved": "close_approval",
    "CompactionStart": "platform_span",
    "CompactionEnd": "close_compaction",
    "MemoryRecall": "search_memory",
    "MemoryInjected": "event_on_turn",
    "MemoryContext": "event_on_turn",
    "BackgroundResults": "event_on_turn",
    "HandProvisioned": "event_on_turn",
    "HandLost": "event_on_turn",
    "SubagentStart": "nested_invoke",
    "SubagentEnd": "close_nested",
    "JobStart": "platform_span",
    "JobEnd": "close_job",
}


class SpanSink(Protocol):
    def export(self, spans: list[SpanRecord]) -> None: ...


@dataclass
class _OpenSpan:
    record: SpanRecord
    kind: str


@dataclass
class JournalProjector:
    sink: SpanSink
    tenant_id: str
    harness_id: str
    harness_version: str
    provider_name: str = "yal"
    _run: _OpenSpan | None = field(default=None, init=False, repr=False)
    _turn: _OpenSpan | None = field(default=None, init=False, repr=False)
    _tools: dict[str, _OpenSpan] = field(default_factory=dict, init=False, repr=False)
    _approvals: dict[str, _OpenSpan] = field(default_factory=dict, init=False, repr=False)
    _compactions: dict[str, _OpenSpan] = field(default_factory=dict, init=False, repr=False)
    _jobs: dict[str, _OpenSpan] = field(default_factory=dict, init=False, repr=False)
    _subagents: dict[str, _OpenSpan] = field(default_factory=dict, init=False, repr=False)
    _pending: list[SpanRecord] = field(default_factory=list, init=False, repr=False)
    _last_event_ms: int | None = field(default=None, init=False, repr=False)
    _closed_run_ids: set[str] = field(default_factory=set, init=False, repr=False)
    closed_by_counts: dict[str, int] = field(default_factory=dict, init=False)

    def project(self, env: Envelope) -> None:
        try:
            self._last_event_ms = env.ts_ms
            self._project(env)
            self._flush()
        except Exception:
            logger.exception("projector failed seq=%s; continuing", env.seq)

    def project_many(self, envs: list[Envelope]) -> None:
        for env in envs:
            self.project(env)

    @property
    def has_open_run(self) -> bool:
        return self._run is not None

    @property
    def open_run_id(self) -> str | None:
        return self._run.record.yal_run_id if self._run else None

    @property
    def run_start_ms(self) -> int | None:
        return self._run.record.start_ms if self._run else None

    @property
    def last_event_ms(self) -> int | None:
        return self._last_event_ms

    def force_close(self, *, end_ms: int, closed_by: str) -> bool:
        """Cascade-close open run. Idempotent by run_id. Returns True if closed."""
        if self._run is None:
            return False
        run_id = self._run.record.yal_run_id
        if run_id in self._closed_run_ids:
            return False
        self._cascade_close_open(end_ms=end_ms, closed_by=closed_by)
        self._run.record.end_ms = end_ms
        self._run.record.status = SpanStatus.UNSET
        self._run.record.attributes["yal.outcome"] = "unknown"
        self._run.record.attributes["yal.closed_by"] = closed_by
        self._emit(self._run.record)
        self._closed_run_ids.add(run_id)
        self.closed_by_counts[closed_by] = self.closed_by_counts.get(closed_by, 0) + 1
        self._run = None
        self._flush()
        return True

    def reconstruct(self, envs: list[Envelope]) -> None:
        """Rebuild open-span state from journal (survives projector restart)."""
        self._run = None
        self._turn = None
        self._tools.clear()
        self._approvals.clear()
        self._compactions.clear()
        self._jobs.clear()
        self._subagents.clear()
        self.project_many(envs)

    def _flush(self) -> None:
        if not self._pending:
            return
        batch = self._pending
        self._pending = []
        try:
            self.sink.export(batch)
        except Exception:
            logger.exception("span export failed; dropping %s spans", len(batch))

    def _base_attrs(self, env: Envelope, run_id: str) -> dict[str, Any]:
        return {
            "yal.tenant.id": self.tenant_id,
            "yal.session.id": env.session_id,
            "yal.run.id": run_id,
            "yal.harness.id": self.harness_id,
            "yal.harness.version": self.harness_version,
            "yal.journal.seq": env.seq,
        }

    def _new_span(
        self,
        *,
        env: Envelope,
        name: str,
        run_id: str,
        attributes: dict[str, Any],
        parent_span_id: str | None,
    ) -> SpanRecord:
        attrs = {**self._base_attrs(env, run_id), **attributes}
        if env.turn_id:
            attrs.setdefault("yal.turn.id", env.turn_id)
        return SpanRecord(
            name=name,
            yal_tenant_id=self.tenant_id,
            yal_session_id=env.session_id,
            yal_run_id=run_id,
            yal_harness_id=self.harness_id,
            yal_harness_version=self.harness_version,
            attributes=attrs,
            status=SpanStatus.UNSET,
            parent_span_id=parent_span_id,
            span_id=uuid4().hex[:16],
            start_ms=env.ts_ms,
            journal_seq=env.seq,
        )

    def _emit(self, span: SpanRecord) -> None:
        self._pending.append(span)

    def _project(self, env: Envelope) -> None:
        p = env.payload
        if isinstance(p, RunStart):
            self._on_run_start(env)
        elif isinstance(p, RunEnd):
            self._on_run_end(env)
        elif isinstance(p, TurnStart):
            self._on_turn_start(env)
        elif isinstance(p, TurnEnd):
            self._on_turn_end(env)
        elif isinstance(p, Settled):
            pass  # event-only on turn
        elif isinstance(p, MessageEnd):
            self._on_message_end(env, p)
        elif isinstance(p, ToolStart):
            self._on_tool_start(env, p)
        elif isinstance(p, ToolEnd):
            self._on_tool_end(env, p)
        elif isinstance(p, ErrorEvent):
            self._on_error(env, p)
        elif isinstance(p, ApprovalRequired):
            self._on_approval_start(env, p)
        elif isinstance(p, ApprovalResolved):
            self._on_approval_end(env, p)
        elif isinstance(p, CompactionStart):
            self._on_compaction_start(env, p)
        elif isinstance(p, CompactionEnd):
            self._on_compaction_end(env, p)
        elif isinstance(p, MemoryRecall):
            self._on_memory_recall(env, p)
        elif isinstance(p, SubagentStart):
            self._on_subagent_start(env, p)
        elif isinstance(p, SubagentEnd):
            self._on_subagent_end(env, p)
        elif isinstance(p, JobStart):
            self._on_job_start(env, p)
        elif isinstance(p, JobEnd):
            self._on_job_end(env, p)

    def _run_id(self, env: Envelope) -> str:
        return resolve_run_id(env)

    def _on_run_start(self, env: Envelope) -> None:
        run_id = self._run_id(env)
        span = self._new_span(
            env=env,
            name=f"invoke_agent {self.harness_id}",
            run_id=run_id,
            attributes={
                "gen_ai.operation.name": GenAIOperation.INVOKE_AGENT,
                "gen_ai.provider.name": self.provider_name,
            },
            parent_span_id=None,
        )
        self._run = _OpenSpan(span, "run")
        self._emit(span)

    def _cascade_close_open(self, *, end_ms: int, closed_by: str) -> None:
        for store in (self._tools, self._approvals, self._compactions, self._jobs, self._subagents):
            for key, open_s in list(store.items()):
                open_s.record.end_ms = end_ms
                open_s.record.status = SpanStatus.UNSET
                open_s.record.attributes["yal.outcome"] = "unknown"
                open_s.record.attributes["yal.closed_by"] = closed_by
                if open_s.kind == "tool":
                    open_s.record.attributes["yal.tool.outcome"] = "unknown"
                    open_s.record.attributes["yal.tool.unpaired"] = True
                self._emit(open_s.record)  # re-export so OTLP can end the span
                del store[key]
        if self._turn is not None:
            self._turn.record.end_ms = end_ms
            self._turn.record.attributes["yal.closed_by"] = closed_by
            self._turn.record.attributes["yal.outcome"] = "unknown"
            self._emit(self._turn.record)
            self._turn = None

    def _on_run_end(self, env: Envelope) -> None:
        self._cascade_close_open(end_ms=env.ts_ms, closed_by="run_end")
        if self._run is not None:
            run_id = self._run.record.yal_run_id
            self._run.record.end_ms = env.ts_ms
            self._run.record.status = SpanStatus.OK
            self._run.record.attributes["yal.closed_by"] = "terminal"
            self._emit(self._run.record)
            self._closed_run_ids.add(run_id)
            self.closed_by_counts["terminal"] = self.closed_by_counts.get("terminal", 0) + 1
            self._run = None

    def _on_turn_start(self, env: Envelope) -> None:
        run_id = self._run_id(env)
        parent = self._run.record.span_id if self._run else None
        span = self._new_span(
            env=env,
            name="yal.turn",
            run_id=run_id,
            attributes={"yal.operation.name": PlatformOperation.TURN},
            parent_span_id=parent,
        )
        self._turn = _OpenSpan(span, "turn")
        self._emit(span)

    def _on_turn_end(self, env: Envelope) -> None:
        if self._turn is not None:
            self._turn.record.end_ms = env.ts_ms
            self._turn.record.status = SpanStatus.OK
            self._emit(self._turn.record)
            self._turn = None

    def _on_message_end(self, env: Envelope, p: MessageEnd) -> None:
        run_id = self._run_id(env)
        parent = self._turn.record.span_id if self._turn else (
            self._run.record.span_id if self._run else None
        )
        attrs: dict[str, Any] = {
            "gen_ai.operation.name": GenAIOperation.CHAT,
            "gen_ai.provider.name": self.provider_name,
        }
        if p.usage is not None:
            attrs.update(to_genai_tokens(p.usage))
            attrs.update(to_yal_cost(p.usage))
        # content intentionally omitted
        span = self._new_span(
            env=env,
            name="chat",
            run_id=run_id,
            attributes=attrs,
            parent_span_id=parent,
        )
        span.end_ms = env.ts_ms
        span.status = SpanStatus.OK
        self._emit(span)

    def _on_tool_start(self, env: Envelope, p: ToolStart) -> None:
        run_id = self._run_id(env)
        parent = self._turn.record.span_id if self._turn else (
            self._run.record.span_id if self._run else None
        )
        span = self._new_span(
            env=env,
            name=f"execute_tool {p.tool_name}",
            run_id=run_id,
            attributes={
                "gen_ai.operation.name": GenAIOperation.EXECUTE_TOOL,
                "gen_ai.provider.name": self.provider_name,
                "gen_ai.tool.name": p.tool_name,
                "tool_call_id": p.tool_call_id,
            },
            parent_span_id=parent,
        )
        self._tools[p.tool_call_id] = _OpenSpan(span, "tool")
        self._emit(span)

    def _on_tool_end(self, env: Envelope, p: ToolEnd) -> None:
        open_s = self._tools.pop(p.tool_call_id, None)
        if open_s is None:
            return
        open_s.record.end_ms = env.ts_ms
        open_s.record.attributes["yal.tool.outcome"] = "error" if p.is_error else "ok"
        open_s.record.status = SpanStatus.ERROR if p.is_error else SpanStatus.OK
        self._emit(open_s.record)

    def _on_error(self, env: Envelope, p: ErrorEvent) -> None:
        target = self._turn or self._run
        if target is None:
            return
        target.record.status = SpanStatus.ERROR
        target.record.attributes["error.type"] = p.error_class

    def _on_approval_start(self, env: Envelope, p: ApprovalRequired) -> None:
        run_id = self._run_id(env)
        parent = self._turn.record.span_id if self._turn else (
            self._run.record.span_id if self._run else None
        )
        span = self._new_span(
            env=env,
            name="yal.approval",
            run_id=run_id,
            attributes={"yal.operation.name": PlatformOperation.APPROVAL},
            parent_span_id=parent,
        )
        self._approvals[p.approval_id] = _OpenSpan(span, "approval")
        self._emit(span)

    def _on_approval_end(self, env: Envelope, p: ApprovalResolved) -> None:
        open_s = self._approvals.pop(p.approval_id, None)
        if open_s is None:
            return
        open_s.record.end_ms = env.ts_ms
        open_s.record.attributes["yal.approval.decision"] = p.choice
        open_s.record.status = SpanStatus.OK
        self._emit(open_s.record)

    def _on_compaction_start(self, env: Envelope, p: CompactionStart) -> None:
        run_id = self._run_id(env)
        parent = self._run.record.span_id if self._run else None
        span = self._new_span(
            env=env,
            name="yal.compaction",
            run_id=run_id,
            attributes={"yal.operation.name": PlatformOperation.COMPACTION},
            parent_span_id=parent,
        )
        self._compactions[p.compaction_id] = _OpenSpan(span, "compaction")
        self._emit(span)

    def _on_compaction_end(self, env: Envelope, p: CompactionEnd) -> None:
        open_s = self._compactions.pop(p.compaction_id, None)
        if open_s is None:
            return
        open_s.record.end_ms = env.ts_ms
        open_s.record.status = SpanStatus.OK
        self._emit(open_s.record)

    def _on_memory_recall(self, env: Envelope, p: MemoryRecall) -> None:
        run_id = self._run_id(env)
        parent = self._turn.record.span_id if self._turn else (
            self._run.record.span_id if self._run else None
        )
        span = self._new_span(
            env=env,
            name="search_memory",
            run_id=run_id,
            attributes={
                "gen_ai.operation.name": GenAIOperation.SEARCH_MEMORY,
                "gen_ai.provider.name": self.provider_name,
            },
            parent_span_id=parent,
        )
        span.end_ms = env.ts_ms
        span.status = SpanStatus.OK
        self._emit(span)

    def _on_subagent_start(self, env: Envelope, p: SubagentStart) -> None:
        run_id = resolve_run_id(env)
        parent = self._run.record.span_id if self._run else None
        span = self._new_span(
            env=env,
            name=f"invoke_agent {p.label or p.provider}",
            run_id=run_id,
            attributes={
                "gen_ai.operation.name": GenAIOperation.INVOKE_AGENT,
                "gen_ai.provider.name": self.provider_name,
            },
            parent_span_id=parent,
        )
        self._subagents[p.run_id] = _OpenSpan(span, "subagent")
        self._emit(span)

    def _on_subagent_end(self, env: Envelope, p: SubagentEnd) -> None:
        open_s = self._subagents.pop(p.run_id, None)
        if open_s is None:
            return
        open_s.record.end_ms = env.ts_ms
        open_s.record.status = SpanStatus.OK
        self._emit(open_s.record)

    def _on_job_start(self, env: Envelope, p: JobStart) -> None:
        run_id = self._run_id(env)
        parent = self._run.record.span_id if self._run else None
        span = self._new_span(
            env=env,
            name="yal.job",
            run_id=run_id,
            attributes={"yal.operation.name": PlatformOperation.JOB},
            parent_span_id=parent,
        )
        self._jobs[p.job_id] = _OpenSpan(span, "job")
        self._emit(span)

    def _on_job_end(self, env: Envelope, p: JobEnd) -> None:
        open_s = self._jobs.pop(p.job_id, None)
        if open_s is None:
            return
        open_s.record.end_ms = env.ts_ms
        open_s.record.status = SpanStatus.OK
        self._emit(open_s.record)


def backfill(projector: JournalProjector, envs: list[Envelope]) -> None:
    """Replay a journal range into the projector."""
    projector.project_many(envs)
