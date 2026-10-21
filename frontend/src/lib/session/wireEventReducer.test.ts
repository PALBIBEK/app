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
