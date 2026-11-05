import { describe, expect, it } from 'vitest';

import type { ToolCallView } from '@/lib/api/types/session.types';

import { reportCalls, reportChanges, reportPlan, surfaceOf, type A2uiMessage } from './reports';

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
