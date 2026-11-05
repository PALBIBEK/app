import { describe, expect, it } from 'vitest';

import type { ToolCallView } from '@/lib/api/types/session.types';
import type { ChatMessage } from '@/lib/api/types/session.types';

import { answeredSurfaces, describeUiAction, reportCalls, reportChanges, reportPlan, surfaceOf, uiActionOf, type A2uiMessage } from './reports';

const V = 'v0.9';
const create = (surfaceId: string): A2uiMessage => ({ version: V, createSurface: { surfaceId, catalogId: 'basic' } });
const update = (surfaceId: string): A2uiMessage => ({ version: V, updateDataModel: { surfaceId, path: '/', value: {} } });
const remove = (surfaceId: string): A2uiMessage => ({ version: V, deleteSurface: { surfaceId } });
const showUi = (id: string, messages: unknown, status: ToolCallView['status'] = 'success'): ToolCallView => ({
  id,
  name: 'show_ui',
  status,
  args: { messages },
});

describe('a conversation’s reports', () => {
  it('are its successful show_ui calls, in order', () => {
    const tools: ToolCallView[] = [
      showUi('a', [create('r')]),
      { id: 'b', name: 'bash', status: 'success', args: { messages: [create('x')] } },
      showUi('c', [create('s')], 'error'),
      showUi('d', [create('t')], 'running'),
      showUi('e', 'not a list'),
      showUi('f', [{ createSurface: {} }]),
      showUi('g', [update('r')]),
    ];
    expect(reportCalls(tools).map((c) => c.id)).toEqual(['a', 'g']);
  });

  it('know the surface each message is about', () => {
    expect([create('r'), update('r'), remove('r')].map(surfaceOf)).toEqual(['r', 'r', 'r']);
    expect(surfaceOf({ version: V, updateComponents: { surfaceId: 's', components: [] } })).toBe('s');
    expect(surfaceOf({ version: V })).toBeNull();
  });

  it('say what each call did to the surfaces', () => {
    const call = { id: 'a', messages: [create('r'), update('r'), update('s'), remove('t'), create('u'), remove('u')] };
    expect(reportChanges(call)).toEqual({ created: ['r'], updated: ['s'], deleted: ['t', 'u'] });
  });

  it('draw a surface where the call that last created it sits', () => {
    const plan = reportPlan([
      { id: 'a', messages: [create('r'), create('s')] },
      { id: 'b', messages: [update('r')] },
      { id: 'c', messages: [remove('s'), create('s')] },
    ]);
    expect(Object.fromEntries(plan.owners)).toEqual({ r: 'a', s: 'c' });
    expect(plan.changes.get('b')).toEqual({ created: [], updated: ['r'], deleted: [] });
  });
});

describe('a person’s answer', () => {
  const said = (surfaceId: string, context: Record<string, unknown> = { pick: ['a'] }): string =>
    `UI action: ${JSON.stringify({ context, name: 'chosen', sourceComponentId: 'go', surfaceId })}`;
  const user = (content: string): ChatMessage => ({ id: content, role: 'user', content, timestamp: '' });
  const drew = (id: string, messages: A2uiMessage[]): ChatMessage => ({
    id,
    role: 'assistant',
    content: '',
    timestamp: '',
    toolCalls: [showUi(id, messages)],
  });

  it('is read from the message the agent was told', () => {
    expect(uiActionOf(said('r'))).toEqual({ surfaceId: 'r', sourceComponentId: 'go', name: 'chosen', context: { pick: ['a'] } });
    expect(uiActionOf('UI action: not json')).toBeNull();
    expect(uiActionOf('UI action: {"name": "x"}')).toBeNull();
    expect(uiActionOf('a message about a UI action')).toBeNull();
  });

  it('reads as what was sent', () => {
    expect(describeUiAction(uiActionOf(said('r', { pick: ['a', 'b'], note: 'soon', qty: 2 }))!)).toBe('Sent “chosen” (pick: a, b; note: soon; qty: 2)');
    expect(describeUiAction(uiActionOf(said('r', {}))!)).toBe('Sent “chosen”');
  });

  it('closes its surface until the agent draws it again', () => {
    const conversation = [drew('a', [create('r'), create('s')]), user(said('r')), user(said('s'))];
    expect([...answeredSurfaces(conversation).keys()]).toEqual(['r', 's']);
    expect([...answeredSurfaces([...conversation, drew('b', [update('r')])]).keys()]).toEqual(['s']);
  });
});
