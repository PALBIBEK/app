'use client';

/**
 * A run's spans as an indented tree with a waterfall: one row per span, its bar placed on
 * the run's timeline. Failures mark the row and every row above it; the slowest and the
 * costliest call are labelled; any branch folds. Long runs open with their healthy steps
 * folded, so where it went wrong is on screen first.
 */

import { CaretDown, CaretRight } from '@phosphor-icons/react';
import { useMemo, useState } from 'react';

import type { RunTrace } from '@/lib/api/types/trace.types';
import { KIND, LIVE_STATUSES, formatMs } from '@/lib/traces/format';
import { buildTree, timeWindow, visibleRows, type SpanNode } from '@/lib/traces/tree';
import { cn } from '@/lib/utils';

/** Spans in a run beyond which healthy steps start folded. */
export const FOLD_STEPS_ABOVE = 60;

function initialFolds(roots: SpanNode[], count: number): Set<string> {
  const folded = new Set<string>();
  if (count <= FOLD_STEPS_ABOVE) return folded;
  const walk = (list: SpanNode[]): void => {
    for (const n of list) {
      if (n.span.kind === 'step' && !n.span.error_below && n.span.status !== 'error') folded.add(n.span.id);
      walk(n.children);
    }
  };
  walk(roots);
  return folded;
}

function Badge({ children, tone }: { children: React.ReactNode; tone: 'bad' | 'warn' | 'quiet' }): JSX.Element {
  return (
    <span
      className={cn(
        'flex-none rounded-full px-1.5 py-px text-[11px]',
        tone === 'bad' && 'bg-red-50 text-red-800',
        tone === 'warn' && 'bg-amber-50 text-amber-900',
        tone === 'quiet' && 'bg-slate-100 text-slate-700',
      )}
    >
      {children}
    </span>
  );
}

export function SpanTree({
  trace,
  selectedId,
  onSelect,
  now,
}: {
  trace: RunTrace;
  selectedId: string | null;
  onSelect: (spanId: string) => void;
  now: number;
}): JSX.Element {
  const roots = useMemo(() => buildTree(trace.spans), [trace.spans]);
  const [folded, setFolded] = useState<Set<string>>(() => initialFolds(roots, trace.spans.length));
  const rows = visibleRows(roots, folded);
  const live = LIVE_STATUSES.includes(trace.rollup.status);
  const win = timeWindow(trace.spans, now);
  const span = win.end - win.start;
  const { slowest_span_id: slowest, costliest_span_id: costliest } = trace.rollup;
  const flagged = new Set((trace.scores ?? []).filter((c) => c.source === 'check' && c.label === 'fail' && c.span_id).map((c) => c.span_id));
  const judged = new Map((trace.scores ?? []).filter((c) => c.source === 'person' && c.span_id).map((c) => [c.span_id, c.label]));

  const toggle = (id: string): void =>
    setFolded((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  const ticks = [0, 0.25, 0.5, 0.75, 1].map((f) => formatMs(f * span));

  return (
    <section aria-label="Spans" className="min-w-0 overflow-hidden rounded-card border border-line bg-surface" data-testid="span-tree">
      <div className="grid grid-cols-[minmax(0,1fr)_72px] gap-3 border-b border-line-soft px-4 py-2.5 text-xs text-ink-3 md:grid-cols-[minmax(0,1fr)_72px_minmax(0,1fr)]">
        <span>Span</span>
        <span className="text-right">Time</span>
        <span className="hidden justify-between md:flex" aria-hidden="true">
          {ticks.map((t, i) => (
            <span key={i}>{i === 0 ? '0' : t}</span>
          ))}
        </span>
      </div>
      <ul>
        {rows.map((node) => {
          const s = node.span;
          const k = KIND[s.kind] ?? KIND.job;
          const hasChildren = node.children.length > 0;
          const isFolded = folded.has(s.id);
          const end = s.end_ms ?? (live ? now : s.start_ms);
          const left = ((s.start_ms - win.start) / span) * 100;
          const width = Math.max(((end - s.start_ms) / span) * 100, 0.4);
          const selected = s.id === selectedId;
          return (
            <li
              key={s.id}
              className={cn('flex items-stretch border-b border-canvas', selected && 'bg-accent-50 shadow-[inset_3px_0_0_theme(colors.accent.500)]')}
              data-testid="span-row"
              data-kind={s.kind}
              data-status={s.status}
            >
              <span className="flex flex-none items-center" style={{ paddingLeft: 8 + node.depth * 16 }}>
                {hasChildren ? (
                  <button
                    type="button"
                    aria-label={isFolded ? `Show what is under ${s.name}` : `Fold ${s.name}`}
                    aria-expanded={!isFolded}
                    onClick={() => toggle(s.id)}
                    className="flex h-7 w-6 items-center justify-center rounded text-ink-3 hover:bg-line-soft hover:text-ink"
                  >
                    {isFolded ? <CaretRight size={12} /> : <CaretDown size={12} />}
                  </button>
                ) : (
                  <span className="w-6" />
                )}
              </span>
              <button
                type="button"
                onClick={() => onSelect(s.id)}
                aria-current={selected ? 'true' : undefined}
                className="grid min-w-0 flex-1 grid-cols-[minmax(0,1fr)_72px] items-center gap-3 py-1.5 pr-4 text-left text-[13px] hover:bg-canvas/60 md:grid-cols-[minmax(0,1fr)_72px_minmax(0,1fr)]"
              >
                <span className="flex min-w-0 items-center gap-2">
                  <span className={cn('flex-none rounded-[5px] px-1.5 py-px text-[10.5px] font-semibold uppercase tracking-[0.04em]', k.chip)}>
                    {k.label}
                  </span>
                  <span className="flex-none whitespace-nowrap font-medium">
                    {s.kind === 'agent' && !s.parent_id ? trace.agent_name ?? s.name : s.name}
                  </span>
                  {s.preview ? <span className="min-w-0 truncate text-ink-3">{s.preview}</span> : null}
                  {s.status === 'error' ? <Badge tone="bad">failed</Badge> : null}
                  {flagged.has(s.id) && s.status !== 'error' ? <Badge tone="bad">check failed</Badge> : null}
                  {judged.get(s.id) === 'bad' ? <Badge tone="bad">marked wrong</Badge> : null}
                  {judged.get(s.id) === 'good' ? <Badge tone="quiet">marked good</Badge> : null}
                  {s.error_below && s.status !== 'error' && isFolded ? <Badge tone="bad">failure inside</Badge> : null}
                  {s.attrs.denied && s.kind === 'tool' ? <Badge tone="quiet">not run</Badge> : null}
                  {s.status === 'unfinished' ? <Badge tone="quiet">unfinished</Badge> : null}
                  {s.id === slowest ? <Badge tone="warn">slowest</Badge> : null}
                  {s.id === costliest ? <Badge tone="warn">costliest</Badge> : null}
                  {isFolded ? <span className="flex-none text-xs text-ink-3">+{node.children.length}</span> : null}
                </span>
                <span className="tabular text-right font-mono text-xs text-ink-2">
                  {s.end_ms == null && live ? 'going' : formatMs(s.duration_ms)}
                </span>
                <span className="relative hidden h-[18px] md:block" aria-hidden="true">
                  <span className="absolute inset-x-0 top-2 h-px bg-line-soft" />
                  <span
                    className={cn('absolute top-1 h-2.5 rounded-[3px]', s.status === 'error' ? 'bg-red-500' : k.bar, s.end_ms == null && 'opacity-60')}
                    style={{ left: `${left}%`, width: `${width}%` }}
                  />
                </span>
              </button>
            </li>
          );
        })}
      </ul>
    </section>
  );
}
