import { describe, expect, it } from 'vitest';

import type { Subagent, ToolCallView } from '@/lib/api/types/session.types';

import { anyActive, noticeHeadline, statusLabel, subagentCall, subagentName, treeOrder } from './subagents';

const call = (over: Partial<ToolCallView>): ToolCallView => ({ id: 'c1', name: 'subagent', status: 'running', ...over });

const sub = (over: Partial<Subagent>): Subagent => ({
  id: 's',
  parent_id: 'root',
  depth: 1,
  kind: 'foreground',
  mode: 'spawn',
  status: 'completed',
  answer_truncated: false,
  open: false,
  cost_usd: 0,
  created_at: '2026-10-31T00:00:00Z',
  ...over,
});

describe('subagentCall', () => {
  it('is null for a call that starts no subagent', () => {
    expect(subagentCall(call({ name: 'bash' }))).toBeNull();
  });

  it('reads a call the agent waits on: its task, its label, and once it answers, the answer', () => {
    const args = { prompt: '  summarise the repo ', description: 'summary' };
    expect(subagentCall(call({ args }), 'child-1')).toMatchObject({
      subagentId: 'child-1',
      task: 'summarise the repo',
      label: 'summary',
      kind: 'foreground',
      state: 'working',
      answer: null,
    });
    const done = subagentCall(call({ args, status: 'success', output: { subagent_id: 'child-1', text: 'three files' } }));
    expect(done).toMatchObject({ state: 'answered', answer: 'three files', subagentId: 'child-1' });
  });

  it('says why a call failed', () => {
    const failed = subagentCall(call({ status: 'error', error: 'the subagent ran out of room before it finished' }));
    expect(failed).toMatchObject({ state: 'failed', answer: 'the subagent ran out of room before it finished' });
  });

  it('reads background, continuable and fork calls as started, not answered', () => {
    const background = subagentCall(
      call({ args: { prompt: 'p', run_in_background: true }, status: 'success', output: { job_id: 'j1', status: 'started' } }),
    );
    expect(background).toMatchObject({ kind: 'background', state: 'started', subagentId: 'j1', answer: null });
    const kept = subagentCall(call({ name: 'subagent_continuable', args: { prompt: 'p' }, status: 'success', output: { subagent_id: 'k1' } }));
    expect(kept).toMatchObject({ kind: 'continuable', state: 'started', subagentId: 'k1' });
    expect(subagentCall(call({ name: 'subagent_fork', args: { prompt: 'p' } }))?.forked).toBe(true);
  });
});

describe('words for a subagent', () => {
  it('goes by its label, else the start of its task', () => {
    expect(subagentName({ label: 'helper', task: 'x' })).toBe('helper');
    expect(subagentName({ label: '', task: 'find the bug\nthen fix it' })).toBe('find the bug');
    expect(subagentName({ task: 'x'.repeat(80) })).toBe(`${'x'.repeat(60)}…`);
    expect(subagentName({})).toBe('Subagent');
  });

  it('says where it stands and what a notice means', () => {
    expect(statusLabel('ready')).toBe('Waiting for a message');
    expect(statusLabel('failed')).toBe('Did not finish');
    expect(noticeHeadline({ subagentId: 's', kind: 'report', text: '' })).toBe('sent an update');
    expect(noticeHeadline({ subagentId: 's', kind: 'settled', status: 'cancelled', text: '' })).toBe('was stopped');
    expect(noticeHeadline({ subagentId: 's', kind: 'settled', status: 'completed', text: '' })).toBe('finished');
  });
});

describe('treeOrder', () => {
  it('puts each subagent right after the one that started it', () => {
    const listed = [
      sub({ id: 'a', parent_id: 'root' }),
      sub({ id: 'b', parent_id: 'root' }),
      sub({ id: 'a1', parent_id: 'a', depth: 2 }),
      sub({ id: 'a1x', parent_id: 'a1', depth: 3 }),
    ];
    expect(treeOrder(listed).map((s) => s.id)).toEqual(['a', 'a1', 'a1x', 'b']);
  });

  it('knows when the listing still changes on its own', () => {
    expect(anyActive([sub({ status: 'completed' }), sub({ status: 'ready' })])).toBe(false);
    expect(anyActive([sub({ status: 'starting' })])).toBe(true);
  });
});
