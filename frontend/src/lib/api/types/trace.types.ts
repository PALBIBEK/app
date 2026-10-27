/**
 * Run traces: each run of a session as a tree of typed spans (built by the supervisor from
 * the session's journal), named by the user's session, agent and version through the BFF.
 */

export type RunStatus =
  | 'running'
  | 'awaiting_approval'
  | 'ok'
  | 'error'
  | 'cancelled'
  | 'budget_exceeded'
  | 'interrupted';

export type SpanKind = 'agent' | 'step' | 'llm' | 'tool' | 'approval' | 'memory' | 'compaction' | 'job';

export type SpanStatus = 'ok' | 'error' | 'running' | 'unfinished';

/** Where a run's wall time went; overlaps count once (human, then tool, then model). */
export interface TimeSplit {
  model_ms: number;
  tool_ms: number;
  human_ms: number;
  other_ms: number;
}

export interface RunSummary {
  session_id: string;
  run_id: string;
  trace_id: string;
  status: RunStatus;
  reason: string | null;
  started_ms: number;
  ended_ms: number | null;
  duration_ms: number | null;
  agent_id: string | null;
  agent_name: string | null;
  version: number | null;
  session_title: string | null;
  input: string | null;
  output: string | null;
  cost: number;
  input_tokens: number;
  output_tokens: number;
  steps: number;
  tool_calls: number;
  tool_errors: number;
  denials: number;
  errors: number;
  checks_failed: number;
  feedback_good: number;
  feedback_bad: number;
  time: Partial<TimeSplit>;
  tools: Record<string, number>;
  models: string[];
}

export interface TraceList {
  items: RunSummary[];
  /** Pass back as `before_ms` for the next page; null at the end. */
  next_before_ms: number | null;
}

export interface SessionTraces {
  session_id: string;
  title: string | null;
  status: string;
  agent_id: string | null;
  agent_name: string | null;
  version: number | null;
  items: RunSummary[];
}

export interface SpanMarker {
  ts_ms: number;
  name: string;
  text: string;
  seq: number;
}

export interface TraceSpan {
  id: string;
  parent_id: string | null;
  kind: SpanKind;
  name: string;
  start_ms: number;
  end_ms: number | null;
  duration_ms: number | null;
  status: SpanStatus;
  error: { class: string; message: string; code?: string } | null;
  error_below: boolean;
  attrs: Record<string, unknown>;
  preview: string | null;
  /** The first part of what reads inline: a reply's text, a command's output (≤ 2,000 chars). */
  excerpt: string | null;
  markers: SpanMarker[];
  seqs: number[];
  /** Only on a span fetched by itself. */
  detail?: Record<string, unknown>;
}

export interface Rollup {
  status: RunStatus;
  reason: string | null;
  started_ms: number;
  ended_ms: number | null;
  duration_ms: number | null;
  time: TimeSplit;
  input: string | null;
  output: string | null;
  cost: number;
  input_tokens: number;
  output_tokens: number;
  cache_read_tokens: number;
  steps: number;
  llm_calls: number;
  tool_calls: number;
  tool_errors: number;
  approvals: number;
  denials: number;
  errors: number;
  models: string[];
  tools: Record<string, number>;
  slowest_span_id: string | null;
  costliest_span_id: string | null;
}

export interface RunTrace {
  session_id: string;
  run_id: string;
  trace_id: string;
  agent_id: string | null;
  agent_name: string | null;
  version: number | null;
  composition_digest: string | null;
  session_title: string | null;
  rollup: Rollup;
  spans: TraceSpan[];
  scores: TraceScore[];
}

/** A verdict on a run (``span_id`` null) or a span: an automatic check or a person's feedback. */
export interface TraceScore {
  id: string;
  span_id: string | null;
  name: string;
  source: 'check' | 'person' | 'judge' | string;
  label: 'pass' | 'fail' | 'good' | 'bad' | string;
  value: number | null;
  reason: string | null;
  comment: string | null;
  evaluator: string;
  /** The user's own feedback. */
  mine: boolean;
  created_ms: number;
}

export interface VersionQuality {
  version: number | null;
  composition_digest: string | null;
  runs: number;
  answered: number;
  failed: number;
  runs_failing_checks: number;
  feedback_good: number;
  feedback_bad: number;
  avg_duration_ms: number | null;
  avg_cost: number;
  last_run_ms: number | null;
}

export interface AgentQuality {
  agent_id: string;
  versions: VersionQuality[];
}

export interface RawEvent {
  seq: number;
  event_id: string;
  ts_ms: number;
  turn_id: string | null;
  event_type: string;
  category: string;
  payload: Record<string, unknown>;
}

export interface TraceListParams {
  status?: RunStatus[];
  agent_id?: string;
  errors?: boolean;
  denied?: boolean;
  failing_checks?: boolean;
  feedback?: 'good' | 'bad';
  q?: string;
  since_ms?: number;
  before_ms?: number;
  limit?: number;
}
