import { describe, expect, it } from 'vitest';

import type { WireEvent } from '@/lib/api/types/session.types';

import { applyWireEvent, createSessionViewState } from './wireEventReducer';

let seq = 0;
const ev = (type: string, data: Record<string, unknown>): WireEvent =>
  ({ seq: ++seq, event_id: `e${seq}`, type, ts_ms: 1_700_000_000_000 + seq, data }) as WireEvent;

describe('applyWireEvent message_end', () => {
  it('confirms a message the page showed when sent instead of showing it twice', () => {
    let state = createSessionViewState();
    state = { ...state, messages: [{ id: 'local-1', role: 'user', content: 'hello', timestamp: 't' }] };
    state = applyWireEvent(state, ev('message_end', { role: 'user', content: [{ type: 'text', text: 'hello' }] }));
    expect(state.messages).toHaveLength(1);
    expect(state.messages[0].id).toMatch(/^e\d+$/);
  });

  it('adds a user message the page did not send (another tab, a replay)', () => {
    const state = applyWireEvent(createSessionViewState(), ev('message_end', { role: 'user', content: 'from elsewhere' }));
    expect(state.messages.map((m) => m.content)).toEqual(['from elsewhere']);
  });

  it('shows no empty bubble for an assistant message that only calls tools', () => {
    const state = applyWireEvent(
      createSessionViewState(),
      ev('message_end', { role: 'assistant', content: [{ type: 'tool_call', id: 'c1', name: 'bash', arguments: {} }] })
    );
    expect(state.messages).toEqual([]);
  });

  it('keeps an assistant text reply', () => {
    const state = applyWireEvent(createSessionViewState(), ev('message_end', { role: 'assistant', content: 'done' }));
    expect(state.messages.map((m) => m.content)).toEqual(['done']);
  });
});

describe('applyWireEvent runs, tools, approvals', () => {
  const run = (...events: WireEvent[]) => events.reduce(applyWireEvent, createSessionViewState());

  it('ignores a replayed event after a reconnect', () => {
    const first = ev('message_end', { role: 'assistant', content: 'once' });
    const state = run(first, first);
    expect(state.messages).toHaveLength(1);
  });

  it('counts model steps per run and adds up usage', () => {
    const state = run(
      ev('run_start', {}),
      ev('message_end', { role: 'assistant', content: 'a', usage: { input_tokens: 10, output_tokens: 2, cost_total: 0.01 } }),
      ev('message_end', { role: 'assistant', content: 'b', usage: { input_tokens: 5, output_tokens: 1, cost_total: 0.02 } }),
    );
    expect(state.running).toBe(true);
    expect(state.runSteps).toBe(2);
    expect(state.usage.input_tokens).toBe(15);
    expect(state.usage.cost).toBeCloseTo(0.03);
    expect(applyWireEvent(state, ev('run_end', {})).running).toBe(false);
  });

  it('explains a refused call in plain words', () => {
    const state = run(
      ev('tool_start', { tool_call_id: 't1', tool_name: 'bash', args: { command: 'ls' } }),
      ev('tool_end', { tool_call_id: 't1', tool_name: 'bash', result: { ok: false, payload: null, error: 'denied by approval', code: 'APPROVAL_DENIED' } }),
    );
    const tool = state.pendingTools.get('t1')!;
    expect(tool.status).toBe('error');
    expect(tool.args).toEqual({ command: 'ls' });
    expect(tool.code).toBe('APPROVAL_DENIED');
    expect(tool.explanation).toMatch(/denied this call/);
  });

  it('keeps a successful payload as the output', () => {
    const state = run(ev('tool_end', { tool_call_id: 't2', tool_name: 'ls', result: { ok: true, payload: { entries: ['a'] } } }));
    expect(state.pendingTools.get('t2')).toMatchObject({ status: 'success', output: { entries: ['a'] } });
  });

  it('gives an approval a deadline for the countdown, and clears it when the run ends', () => {
    const asked = ev('approval_required', { approval_id: 'a1', prompt: 'Allow?', options: ['allow', 'deny'], timeout_s: 300 });
    let state = run(asked);
    expect(state.pendingApprovals[0].deadline_ms).toBe(asked.ts_ms + 300_000);
    state = applyWireEvent(state, ev('run_end', {}));
    expect(state.pendingApprovals).toEqual([]);
  });
});

describe('applyWireEvent hands', () => {
  const ev = (seq: number, type: string, data: Record<string, unknown>) =>
    ({ seq, event_id: `e${seq}`, turn_id: null, type, ts_ms: seq, data }) as never;

  it('follows the default sandbox: ready, stopped when idle, ready again', () => {
    let s = createSessionViewState();
    s = applyWireEvent(s, ev(1, 'hand_provisioned', { hand_id: 'h1', provider: 'local_process', cause: 'prewarm', hand: 'default' }));
    expect(s.sandbox).toEqual({ status: 'ready', cause: 'prewarm' });
    s = applyWireEvent(s, ev(2, 'hand_provisioned', { hand_id: 'h9', cause: 'first_use', hand: 'browser' }));
    expect(s.sandbox?.cause).toBe('prewarm');  // a named hand is not the session's sandbox
    s = applyWireEvent(s, ev(3, 'hand_stopped', { hand_id: 'h1', reason: 'idle', idle_s: 901, hand: 'default' }));
    expect(s.sandbox).toEqual({ status: 'stopped', idle_s: 901 });
    s = applyWireEvent(s, ev(4, 'hand_provisioned', { hand_id: 'h2', cause: 'resumed', hand: 'default' }));
    expect(s.sandbox?.status).toBe('ready');
  });
});

describe('applyWireEvent partial_output', () => {
  const partial = (text: string, last: number): WireEvent =>
    ({ seq: last, event_id: 'partial:r1:0', turn_id: 'r1', type: 'partial_output', ts_ms: 1, data: { step: 0, text, thinking: '' } }) as WireEvent;

  it('shows the reply as it streams and drops it once the journal has the reply', () => {
    let state = applyWireEvent(createSessionViewState(), ev('run_start', {}));
    const last = state.lastSeq;
    state = applyWireEvent(state, partial('The ans', last));
    state = applyWireEvent(state, partial('The answer is 42', last)); // same seq: not a replay
    expect(state.partial?.text).toBe('The answer is 42');
    expect(state.lastSeq).toBe(last); // a partial is not a position to resume from
    state = applyWireEvent(state, ev('message_end', { role: 'assistant', content: [{ type: 'text', text: 'The answer is 42.' }] }));
    expect(state.partial).toBeNull();
    expect(state.messages.map((m) => m.content)).toEqual(['The answer is 42.']);
  });

  it('ignores a partial when no run is open (a late frame after the run ended)', () => {
    const state = applyWireEvent(createSessionViewState(), partial('stale', 0));
    expect(state.partial).toBeNull();
  });
});
