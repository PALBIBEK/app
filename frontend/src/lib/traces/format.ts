/**
 * How a trace's numbers and states read: durations at the precision a person wants,
 * run statuses in words (and the shape the status pill draws), the time split in parts.
 */

import type { StatusKind } from '@/components/ui';
import type { RunStatus, SpanKind, TimeSplit } from '@/lib/api/types/trace.types';

/** 350 ms · 4.2 s · 41.8 s · 2 min 10 s · 1 h 3 min */
export function formatMs(ms: number | null | undefined): string {
  if (ms == null || !Number.isFinite(ms) || ms < 0) return '—';
  if (ms < 1000) return `${Math.round(ms)} ms`;
  const s = ms / 1000;
  if (s < 60) return `${s.toFixed(1)} s`;
  const total = Math.round(s);
  const m = Math.floor(total / 60);
  if (m < 60) return `${m} min ${String(total % 60).padStart(2, '0')} s`;
  return `${Math.floor(m / 60)} h ${m % 60} min`;
}

export const RUN_STATUS: Record<RunStatus, { label: string; kind: StatusKind }> = {
  ok: { label: 'Answered', kind: 'done' },
  error: { label: 'Failed', kind: 'failed' },
  budget_exceeded: { label: 'Out of budget', kind: 'failed' },
  cancelled: { label: 'Stopped', kind: 'stopped' },
  interrupted: { label: 'Interrupted', kind: 'stopped' },
  running: { label: 'Running', kind: 'running' },
  awaiting_approval: { label: 'Waiting on you', kind: 'awaiting' },
};

export function runStatus(status: string): { label: string; kind: StatusKind } {
  return RUN_STATUS[status as RunStatus] ?? { label: status, kind: 'idle' };
}

export const LIVE_STATUSES: readonly RunStatus[] = ['running', 'awaiting_approval'];

/** Each kind's word and colours: a tinted label chip and the bar on the waterfall. */
export const KIND: Record<SpanKind, { label: string; chip: string; bar: string }> = {
  agent: { label: 'Agent', chip: 'bg-slate-200 text-slate-800', bar: 'bg-slate-600' },
  step: { label: 'Step', chip: 'bg-slate-100 text-slate-600', bar: 'bg-slate-300' },
  llm: { label: 'Model', chip: 'bg-indigo-50 text-indigo-800', bar: 'bg-indigo-500' },
  tool: { label: 'Tool', chip: 'bg-teal-50 text-teal-800', bar: 'bg-teal-600' },
  approval: { label: 'Approval', chip: 'bg-amber-50 text-amber-900', bar: 'bg-amber-400 stripes' },
  memory: { label: 'Memory', chip: 'bg-violet-50 text-violet-800', bar: 'bg-violet-500' },
  compaction: { label: 'Compact', chip: 'bg-sky-50 text-sky-800', bar: 'bg-sky-500' },
  job: { label: 'Job', chip: 'bg-slate-100 text-slate-700', bar: 'bg-slate-500' },
};

export interface SplitPart {
  key: keyof TimeSplit;
  label: string;
  ms: number;
  pct: number;
  className: string;
}

const PARTS: readonly { key: keyof TimeSplit; label: string; className: string }[] = [
  { key: 'model_ms', label: 'Model', className: 'bg-indigo-500' },
  { key: 'tool_ms', label: 'Tools', className: 'bg-teal-600' },
  { key: 'human_ms', label: 'Waiting on you', className: 'bg-amber-400 stripes' },
  { key: 'other_ms', label: 'Other', className: 'bg-slate-300' },
];

/** The parts of a run's time that took any, as shares of their sum. */
export function splitParts(time: Partial<TimeSplit> | null | undefined): SplitPart[] {
  const values = PARTS.map((p) => ({ ...p, ms: Math.max(0, time?.[p.key] ?? 0) }));
  const total = values.reduce((sum, p) => sum + p.ms, 0);
  if (total <= 0) return [];
  return values.filter((p) => p.ms > 0).map((p) => ({ ...p, pct: (p.ms / total) * 100 }));
}

/** "grep, read, edit, bash ×2" */
export function toolsLine(tools: Record<string, number> | null | undefined): string {
  return Object.entries(tools ?? {})
    .map(([name, n]) => (n > 1 ? `${name} ×${n}` : name))
    .join(', ');
}
