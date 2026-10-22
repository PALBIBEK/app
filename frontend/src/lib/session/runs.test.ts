import { describe, expect, it } from 'vitest';

import type { Run } from '@/lib/api/types/session.types';

import { runDuration, runToStop } from './runs';

const run = (over: Partial<Run>): Run => ({
  run_id: 'r', command_id: 'c', status: 'completed', created_at_ms: 0, started_at_ms: null, ended_at_ms: null,
  stop_reason: null, output_text: null, error: null, usage: {}, steps: 0, tool_calls: 0, pending_approval_id: null,
  ...over,
});

describe('runs', () => {
  it('Stop cancels the run going now, not a queued or an ended one', () => {
    const runs = [run({ run_id: 'q', status: 'queued' }), run({ run_id: 'g', status: 'requires_action' }), run({ run_id: 'd' })];
    expect(runToStop(runs)?.run_id).toBe('g');
    expect(runToStop([run({ status: 'queued' }), run({})])).toBeUndefined();
  });

  it('labels durations', () => {
    expect(runDuration(run({}))).toBeNull();
    expect(runDuration(run({ started_at_ms: 0, ended_at_ms: 820 }))).toBe('820 ms');
    expect(runDuration(run({ started_at_ms: 0, ended_at_ms: 4200 }))).toBe('4.2 s');
    expect(runDuration(run({ started_at_ms: 0, ended_at_ms: 185_000 }))).toBe('3 m 05 s');
    expect(runDuration(run({ started_at_ms: 1000 }), 3000)).toBe('2.0 s');
  });
});
