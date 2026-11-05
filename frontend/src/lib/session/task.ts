/**
 * The task the agent shows beside the conversation (`update_task`): its goal, its steps with
 * their status, and what it has found. Each call sends the whole task, so the task is the
 * latest successful call's, as the supervisor returned it (checked against its schema).
 */

import type { ChatMessage, ToolCallView } from '../api/types/session.types';

export const UPDATE_TASK = 'update_task';

export type StepStatus = 'todo' | 'doing' | 'done' | 'failed' | 'skipped';
const STATUSES: ReadonlySet<string> = new Set<StepStatus>(['todo', 'doing', 'done', 'failed', 'skipped']);

export interface TaskStep {
  title: string;
  status: StepStatus;
  note?: string;
}

export interface TaskFound {
  label: string;
  value: string;
  source?: string;
}

export interface TaskView {
  goal: string;
  steps: TaskStep[];
  found: TaskFound[];
}

type Fields = Record<string, unknown>;

const fields = (v: unknown): Fields => (v && typeof v === 'object' && !Array.isArray(v) ? (v as Fields) : {});
const text = (v: unknown): string | undefined => (typeof v === 'string' && v ? v : undefined);
const list = (v: unknown): unknown[] => (Array.isArray(v) ? v : []);

/** The task a successful `update_task` call returned, or null for any other call. */
export function taskOfCall(tool: ToolCallView): TaskView | null {
  if (tool.name !== UPDATE_TASK || tool.status !== 'success') return null;
  const task = fields(fields(tool.output).task);
  const goal = text(task.goal);
  if (!goal) return null;
  const steps = list(task.steps).flatMap((raw): TaskStep[] => {
    const s = fields(raw);
    const title = text(s.title);
    const status = STATUSES.has(String(s.status)) ? (s.status as StepStatus) : 'todo';
    return title ? [{ title, status, ...(text(s.note) ? { note: text(s.note) } : {}) }] : [];
  });
  const found = list(task.found).flatMap((raw): TaskFound[] => {
    const f = fields(raw);
    const label = text(f.label);
    const value = text(f.value);
    const source = text(f.source);
    return label && value ? [{ label, value, ...(source && /^https?:\/\//.test(source) ? { source } : {}) }] : [];
  });
  return { goal, steps, found };
}

/** The conversation's task: the latest one shown, or null if it never showed one. */
export function currentTask(messages: readonly ChatMessage[]): TaskView | null {
  for (let i = messages.length - 1; i >= 0; i--) {
    const calls = messages[i].toolCalls ?? [];
    for (let j = calls.length - 1; j >= 0; j--) {
      const task = taskOfCall(calls[j]);
      if (task) return task;
    }
  }
  return null;
}

/** How far along the task is, in a few words. */
export function progress(task: TaskView): string {
  if (!task.steps.length) return task.found.length ? `${task.found.length} found` : 'no steps yet';
  const done = task.steps.filter((s) => s.status === 'done' || s.status === 'skipped').length;
  const failed = task.steps.filter((s) => s.status === 'failed').length;
  return `${done} of ${task.steps.length} done${failed ? `, ${failed} failed` : ''}`;
}
