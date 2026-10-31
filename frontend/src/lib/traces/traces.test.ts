/**
 * The trace view's pure parts: the tree, the transcript's folding, the numbers' wording and
 * the list's query string.
 */
import { describe, expect, it } from 'vitest';

import { traceListQuery } from '@/lib/api/endpoints/traces';

import { sampleRun, span } from './fixture';
import { formatMs, runStatus, splitParts, toolsLine } from './format';
import { buildTranscript } from './transcript';
import { buildTree, timeWindow, visibleRows } from './tree';

describe('tree', () => {
  it('nests spans under their parents in start order and folds branches', () => {
    const roots = buildTree(sampleRun().spans);
    expect(roots).toHaveLength(1);
    expect(roots[0].children.map((c) => c.span.id)).toEqual(['s1', 's2', 's3']);
    expect(roots[0].children[1].children[0].children[0].span.kind).toBe('approval');
    expect(visibleRows(roots, new Set()).map((n) => n.depth)).toEqual([0, 1, 2, 2, 2, 1, 2, 3, 1, 2]);
    expect(visibleRows(roots, new Set(['s1', 's2'])).map((n) => n.span.id)).toEqual(['run', 's1', 's2', 's3', 'l3']);
  });

  it('keeps a span whose parent is missing, as a root', () => {
    const spans = sampleRun().spans.filter((s) => s.id !== 's3');
    expect(buildTree(spans).map((r) => r.span.id)).toEqual(['run', 'l3']);
  });

  it('draws a running run up to now', () => {
    const spans = sampleRun().spans.map((s) => (s.id === 'run' ? { ...s, end_ms: null } : s));
    expect(timeWindow(spans, 50_000)).toEqual({ start: 0, end: 50_000 });
  });
});

describe('transcript', () => {
  it('reads as the ask, folded looking around, the approved command and the answer', () => {
    const items = buildTranscript(sampleRun());
    expect(items.map((i) => i.type)).toEqual(['input', 'step', 'explore', 'step', 'marker', 'tool', 'step', 'say']);
    const explore = items.find((i) => i.type === 'explore');
    expect(explore?.type === 'explore' && explore.spans.map((s) => s.id)).toEqual(['t1', 't2']);
    const tool = items.find((i) => i.type === 'tool');
    expect(tool?.type === 'tool' && tool.approval?.id).toBe('a1');
    const step2 = items.find((i) => i.type === 'step' && i.index === 2);
    expect(step2?.type === 'step' && step2.humanMs).toBe(14_000);
    const say = items.find((i) => i.type === 'say');
    expect(say?.type === 'say' && say.final).toBe(true);
  });

  it('ends with how a run that did not answer ended', () => {
    const items = buildTranscript(sampleRun({ status: 'budget_exceeded', reason: 'cost cap reached' }));
    expect(items.at(-1)).toMatchObject({ type: 'end', status: 'budget_exceeded', reason: 'cost cap reached' });
  });

  it("keeps a background subagent's later words from being the run's answer", () => {
    const run = sampleRun();
    // A background subagent started in step 2 answered after the run's own last word.
    run.spans.push(
      span({ id: 'sub', parent_id: 's2', kind: 'tool', name: 'subagent', start_ms: 3_200, attrs: { tool: 'subagent' }, preview: 'subagent summarise' }),
      span({ id: 'child', parent_id: 'sub', kind: 'agent', name: 'summary', start_ms: 3_300, end_ms: 45_000, duration_ms: 41_700 }),
      span({ id: 'cl', parent_id: 'child', kind: 'llm', name: 'chat m1', start_ms: 44_000, excerpt: 'Summary done.' }),
    );
    const finals = buildTranscript(run).filter((i) => i.type === 'say' && i.final);
    expect(finals.map((i) => i.type === 'say' && i.span.id)).toEqual(['l3']);
  });

  it('shows a single look on its own, not folded', () => {
    const run = sampleRun();
    run.spans = run.spans.filter((s) => s.id !== 't2');
    expect(buildTranscript(run).filter((i) => i.type === 'explore')).toHaveLength(0);
  });
});

describe('format', () => {
  it('words durations at the precision a person wants', () => {
    expect([formatMs(350), formatMs(4_210), formatMs(41_800), formatMs(130_000), formatMs(3_780_000)]).toEqual([
      '350 ms',
      '4.2 s',
      '41.8 s',
      '2 min 10 s',
      '1 h 3 min',
    ]);
    expect(formatMs(null)).toBe('—');
  });

  it('names statuses and splits time into the parts that took any', () => {
    expect(runStatus('awaiting_approval')).toEqual({ label: 'Waiting on you', kind: 'awaiting' });
    expect(runStatus('budget_exceeded').kind).toBe('failed');
    const parts = splitParts({ model_ms: 300, tool_ms: 100, human_ms: 0, other_ms: 100 });
    expect(parts.map((p) => [p.label, Math.round(p.pct)])).toEqual([
      ['Model', 60],
      ['Tools', 20],
      ['Other', 20],
    ]);
    expect(splitParts({})).toEqual([]);
    expect(toolsLine({ grep: 1, bash: 2 })).toBe('grep, bash ×2');
  });
});

describe('tracesAPI', () => {
  it('sends each status as its own key, as the API reads a list', () => {
    expect(traceListQuery({ status: ['ok', 'error'], errors: true, q: ' fix ', limit: 50 })).toBe(
      '?status=ok&status=error&errors=true&q=fix&limit=50',
    );
    expect(traceListQuery()).toBe('');
  });
});
