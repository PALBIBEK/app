/**
 * A run read as a conversation: what was asked, what the agent said, what it did, how it
 * ended. Routine looking around (several reads, listings and searches in a row) folds into
 * one line, model calls that only asked for tools disappear into the tool cards, and each
 * step is marked with its time and the part of it spent waiting on a person.
 */

import type { RunTrace, SpanMarker, TraceSpan } from '@/lib/api/types/trace.types';

import { buildTree, type SpanNode } from './tree';

/** Tools that only look: a run of two or more of them reads as one "explored" line. */
export const LOOKING_TOOLS: ReadonlySet<string> = new Set(['read', 'ls', 'grep', 'glob', 'find']);

export type TranscriptItem =
  | { type: 'input'; key: string; text: string; at: number }
  | { type: 'step'; key: string; index: number; durationMs: number | null; humanMs: number }
  | { type: 'say'; key: string; span: TraceSpan; final: boolean }
  | { type: 'tool'; key: string; span: TraceSpan; approval: TraceSpan | null }
  | { type: 'explore'; key: string; spans: TraceSpan[] }
  | { type: 'other'; key: string; span: TraceSpan }
  | { type: 'marker'; key: string; marker: SpanMarker }
  | { type: 'end'; key: string; status: string; reason: string | null };

function toolName(span: TraceSpan): string {
  return String(span.attrs.tool ?? span.name);
}

function looks(span: TraceSpan): boolean {
  return LOOKING_TOOLS.has(toolName(span)) && span.status === 'ok' && !span.attrs.denied;
}

function approvalOf(node: SpanNode): TraceSpan | null {
  return node.children.find((c) => c.span.kind === 'approval')?.span ?? null;
}

function humanMs(step: SpanNode): number {
  let total = 0;
  const walk = (list: SpanNode[]): void => {
    for (const n of list) {
      if (n.span.kind === 'approval') total += n.span.duration_ms ?? 0;
      walk(n.children);
    }
  };
  walk(step.children);
  return total;
}

/** The items of a step (or of the run, for what sits directly under it), in order. */
function itemsOf(nodes: readonly SpanNode[], lastSay: string | null): TranscriptItem[] {
  const out: TranscriptItem[] = [];
  let looking: TraceSpan[] = [];
  const flush = (): void => {
    if (looking.length >= 2) out.push({ type: 'explore', key: `explore-${looking[0].id}`, spans: looking });
    else for (const s of looking) out.push({ type: 'tool', key: s.id, span: s, approval: null });
    looking = [];
  };
  for (const node of nodes) {
    const span = node.span;
    if (span.kind === 'tool' && looks(span) && !approvalOf(node)) {
      looking.push(span);
      continue;
    }
    flush();
    if (span.kind === 'llm') {
      if (span.excerpt) out.push({ type: 'say', key: span.id, span, final: span.id === lastSay });
    } else if (span.kind === 'tool') {
      out.push({ type: 'tool', key: span.id, span, approval: approvalOf(node) });
    } else if (span.kind !== 'step' && span.kind !== 'approval') {
      out.push({ type: 'other', key: span.id, span });
    }
  }
  flush();
  return out;
}

function markersOf(span: TraceSpan): TranscriptItem[] {
  return span.markers.map((m) => ({ type: 'marker', key: `marker-${m.seq}`, marker: m }) as TranscriptItem);
}

export function buildTranscript(trace: RunTrace): TranscriptItem[] {
  const roots = buildTree(trace.spans);
  const root = roots.find((r) => r.span.kind === 'agent') ?? roots[0];
  if (!root) return [];
  const says = trace.spans.filter((s) => s.kind === 'llm' && s.excerpt);
  const lastSay = says.length ? says[says.length - 1].id : null;
  const out: TranscriptItem[] = [];
  const input = root.span.excerpt ?? trace.rollup.input;
  if (input) out.push({ type: 'input', key: 'input', text: input, at: root.span.start_ms });
  out.push(...markersOf(root.span));

  let loose: SpanNode[] = [];
  const flushLoose = (): void => {
    out.push(...itemsOf(loose, lastSay));
    loose = [];
  };
  for (const child of root.children) {
    if (child.span.kind !== 'step') {
      loose.push(child);
      continue;
    }
    flushLoose();
    out.push({
      type: 'step',
      key: child.span.id,
      index: Number(child.span.attrs.index ?? 0),
      durationMs: child.span.duration_ms,
      humanMs: humanMs(child),
    });
    out.push(...markersOf(child.span));
    out.push(...itemsOf(child.children, lastSay));
  }
  flushLoose();

  const status = trace.rollup.status;
  if (status !== 'ok') out.push({ type: 'end', key: 'end', status, reason: trace.rollup.reason });
  return out;
}
