/**
 * A run's flat span list as a tree, and the rows a tree view shows with some branches folded.
 */

import type { TraceSpan } from '@/lib/api/types/trace.types';

export interface SpanNode {
  span: TraceSpan;
  children: SpanNode[];
  depth: number;
}

function byStart(a: SpanNode, b: SpanNode): number {
  return a.span.start_ms - b.span.start_ms || (a.span.seqs[0] ?? 0) - (b.span.seqs[0] ?? 0);
}

/** The roots (normally one: the run), each child list in start order. A span whose parent is
 * missing becomes a root rather than disappearing. */
export function buildTree(spans: readonly TraceSpan[]): SpanNode[] {
  const nodes = new Map<string, SpanNode>();
  for (const span of spans) nodes.set(span.id, { span, children: [], depth: 0 });
  const roots: SpanNode[] = [];
  for (const node of nodes.values()) {
    const parent = node.span.parent_id ? nodes.get(node.span.parent_id) : undefined;
    if (parent) parent.children.push(node);
    else roots.push(node);
  }
  const place = (list: SpanNode[], depth: number): void => {
    list.sort(byStart);
    for (const n of list) {
      n.depth = depth;
      place(n.children, depth + 1);
    }
  };
  place(roots, 0);
  return roots;
}

/** The rows to draw: every node in order, skipping what sits under a folded node. */
export function visibleRows(roots: readonly SpanNode[], folded: ReadonlySet<string>): SpanNode[] {
  const out: SpanNode[] = [];
  const walk = (list: readonly SpanNode[]): void => {
    for (const n of list) {
      out.push(n);
      if (!folded.has(n.span.id)) walk(n.children);
    }
  };
  walk(roots);
  return out;
}

/** The window the waterfall draws: from the run's start to its end (or the latest span edge,
 * or ``now`` for a run still going). */
export function timeWindow(spans: readonly TraceSpan[], now: number): { start: number; end: number } {
  if (spans.length === 0) return { start: 0, end: 1 };
  const start = Math.min(...spans.map((s) => s.start_ms));
  const end = Math.max(...spans.map((s) => s.end_ms ?? now), start + 1);
  return { start, end };
}
