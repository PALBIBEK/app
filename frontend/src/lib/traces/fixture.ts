/**
 * A small run for tests: asked to fix a test, looked at two files, ran the tests (approved),
 * edited a file, ran them again, answered.
 */

import type { RunTrace, TraceSpan } from '@/lib/api/types/trace.types';

let seq = 0;

export function span(over: Partial<TraceSpan> & Pick<TraceSpan, 'id' | 'kind' | 'name' | 'start_ms'>): TraceSpan {
  seq += 1;
  return {
    parent_id: null,
    end_ms: over.start_ms + 100,
    duration_ms: 100,
    status: 'ok',
    error: null,
    error_below: false,
    attrs: {},
    preview: null,
    excerpt: null,
    markers: [],
    seqs: [seq],
    ...over,
  };
}

export function sampleRun(over: Partial<RunTrace['rollup']> = {}): RunTrace {
  const spans: TraceSpan[] = [
    span({ id: 'run', kind: 'agent', name: 'reviewer', start_ms: 0, end_ms: 40_000, duration_ms: 40_000, excerpt: 'Fix the date test', preview: 'Fix the date test' }),
    span({ id: 's1', parent_id: 'run', kind: 'step', name: 'step 1', start_ms: 0, end_ms: 3_000, duration_ms: 3_000, attrs: { index: 1 } }),
    span({ id: 'l1', parent_id: 's1', kind: 'llm', name: 'chat m1', start_ms: 0, preview: 'asks for grep, read' }),
    span({ id: 't1', parent_id: 's1', kind: 'tool', name: 'grep', start_ms: 1_000, attrs: { tool: 'grep', exec_ms: 50 }, preview: 'grep parse_date' }),
    span({ id: 't2', parent_id: 's1', kind: 'tool', name: 'read', start_ms: 1_100, attrs: { tool: 'read', exec_ms: 40 }, preview: 'read src/dates.py' }),
    span({ id: 's2', parent_id: 'run', kind: 'step', name: 'step 2', start_ms: 3_000, end_ms: 20_000, duration_ms: 17_000, attrs: { index: 2 }, markers: [{ ts_ms: 3_100, name: 'sandbox_started', text: 'default sandbox started on local_docker (first use)', seq: 99 }] }),
    span({ id: 't3', parent_id: 's2', kind: 'tool', name: 'bash', start_ms: 3_100, end_ms: 20_000, duration_ms: 16_900, status: 'error', error: { class: 'ToolError', message: 'exit 1' }, attrs: { tool: 'bash', exec_ms: 2_000, exit_code: 1 }, preview: '$ pytest -q', excerpt: '1 failed, 41 passed' }),
    span({ id: 'a1', parent_id: 't3', kind: 'approval', name: 'approval bash', start_ms: 3_100, end_ms: 17_100, duration_ms: 14_000, attrs: { decision: 'allow', outcome: 'approved', denied: false, responded_by: 'you', wait_ms: 14_000 }, preview: 'allowed after 14s' }),
    span({ id: 's3', parent_id: 'run', kind: 'step', name: 'step 3', start_ms: 20_000, end_ms: 40_000, duration_ms: 20_000, attrs: { index: 3 } }),
    span({ id: 'l3', parent_id: 's3', kind: 'llm', name: 'chat m1', start_ms: 20_000, end_ms: 26_000, duration_ms: 6_000, preview: 'Fixed it.', excerpt: 'Fixed it.', attrs: { cost: 0.01 } }),
  ];
  spans[0].error_below = true;
  spans[5].error_below = true;
  return {
    session_id: 'sess',
    run_id: 'r1',
    trace_id: 't'.repeat(32),
    agent_id: 'h1',
    agent_name: 'reviewer',
    version: 2,
    composition_digest: 'sha256:' + 'a'.repeat(64),
    session_title: 'Fix the parser',
    summary: 'Ran 1 command (1 failed), looked at 2 things; answered.',
    rollup: {
      status: 'ok',
      reason: null,
      started_ms: 0,
      ended_ms: 40_000,
      duration_ms: 40_000,
      time: { model_ms: 8_000, tool_ms: 2_100, human_ms: 14_000, other_ms: 15_900 },
      input: 'Fix the date test',
      output: 'Fixed it.',
      cost: 0.03,
      input_tokens: 1200,
      output_tokens: 80,
      cache_read_tokens: 0,
      steps: 3,
      llm_calls: 2,
      tool_calls: 3,
      tool_errors: 1,
      approvals: 1,
      denials: 0,
      errors: 1,
      models: ['m1'],
      tools: { grep: 1, read: 1, bash: 1 },
      slowest_span_id: 'l3',
      costliest_span_id: 'l3',
      ...over,
    },
    spans,
    scores: [
      { id: 'c1', span_id: 'run', name: 'finished', topic: null, source: 'check', label: 'pass', value: 1, reason: 'answered', comment: null, evaluator: 'checks@1', mine: false, created_ms: 1 },
      { id: 'c2', span_id: 't3', name: 'tool_errors', topic: 'bash', source: 'check', label: 'fail', value: 0, reason: '1 of 3 tool calls failed; first: $ pytest -q (exit 1)', comment: null, evaluator: 'checks@1', mine: false, created_ms: 1 },
    ],
  };
}
