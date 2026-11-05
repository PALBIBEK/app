import { describe, expect, it } from 'vitest';

import type { ChatMessage, ToolCallView } from '@/lib/api/types/session.types';

import { currentTask, progress, taskOfCall } from './task';

const TASK = {
  goal: 'Find the cheapest paper towels',
  steps: [
    { title: 'Search Shop A', status: 'done', note: '£3.00 for 6' },
    { title: 'Search Shop B', status: 'failed' },
    { title: 'Search Shop C', status: 'doing' },
  ],
  found: [{ label: 'Shop A, 6 rolls', value: '£3.00', source: 'https://shop-a.example/towels' }],
};

const call = (id: string, task: unknown, status: ToolCallView['status'] = 'success'): ToolCallView => ({
  id,
  name: 'update_task',
  status,
  output: { task },
});
const said = (id: string, ...toolCalls: ToolCallView[]): ChatMessage => ({ id, role: 'assistant', content: '', timestamp: '', toolCalls });

describe('the task the agent shows', () => {
  it('is what a successful call returned', () => {
    expect(taskOfCall(call('c1', TASK))).toEqual(TASK);
    expect(taskOfCall(call('c2', TASK, 'error'))).toBeNull();
    expect(taskOfCall({ ...call('c3', TASK), name: 'bash' })).toBeNull();
  });

  it('keeps only what it can show safely', () => {
    const task = taskOfCall(
      call('c1', {
        goal: 'g',
        steps: [{ title: 'a', status: 'whatever' }, { status: 'done' }],
        found: [{ label: 'x', value: '1', source: 'javascript:alert(1)' }, { label: 'y' }],
      }),
    );
    expect(task).toEqual({ goal: 'g', steps: [{ title: 'a', status: 'todo' }], found: [{ label: 'x', value: '1' }] });
    expect(taskOfCall(call('c2', { steps: [] }))).toBeNull();
  });

  it('is the latest one shown in the conversation', () => {
    const later = { ...TASK, steps: TASK.steps.map((s) => ({ ...s, status: 'done' })) };
    const messages = [said('m1', call('c1', TASK)), said('m2', call('c2', later), call('c3', later, 'running'))];

    expect(currentTask(messages)).toEqual(later);
    expect(currentTask([said('m0')])).toBeNull();
  });

  it('says how far along it is', () => {
    expect(progress(taskOfCall(call('c1', TASK))!)).toBe('1 of 3 done, 1 failed');
    expect(progress({ goal: 'g', steps: [], found: [] })).toBe('no steps yet');
  });
});
