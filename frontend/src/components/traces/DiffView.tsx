'use client';

/**
 * A file change as lines removed and added. An edit replaces exact text, so its removed and
 * added lines are the whole change; a write has no "before" here and shows what it wrote.
 */

import { cn } from '@/lib/utils';

export interface FileChange {
  path: string;
  removed: string | null;
  added: string;
  replace_all?: boolean;
}

function lines(text: string): string[] {
  const out = text.split('\n');
  return out.length > 1 && out[out.length - 1] === '' ? out.slice(0, -1) : out;
}

export function changeCounts(change: FileChange): { added: number; removed: number } {
  return { added: change.added ? lines(change.added).length : 0, removed: change.removed ? lines(change.removed).length : 0 };
}

export function DiffView({ change, maxLines = 400 }: { change: FileChange; maxLines?: number }): JSX.Element {
  const removed = change.removed ? lines(change.removed) : [];
  const added = change.added ? lines(change.added) : [];
  const rows = [
    ...removed.map((text) => ({ sign: '-', text })),
    ...added.map((text) => ({ sign: '+', text })),
  ];
  const shown = rows.slice(0, maxLines);
  return (
    <div className="overflow-hidden rounded-lg border border-line font-mono text-xs leading-[1.7]">
      <div className="border-b border-line-soft bg-canvas px-3 py-1 text-ink-3">
        {change.removed == null ? `wrote ${change.path}` : `${change.path}${change.replace_all ? ' (every occurrence)' : ''}`}
      </div>
      <div className="overflow-x-auto">
        {shown.map((r, i) => (
          <div
            key={i}
            className={cn(
              'whitespace-pre px-3',
              r.sign === '-' ? 'bg-red-50 text-red-900' : 'bg-emerald-50 text-emerald-900',
            )}
          >
            <span aria-hidden="true" className="mr-2 select-none opacity-60">
              {r.sign}
            </span>
            <span className="sr-only">{r.sign === '-' ? 'removed: ' : 'added: '}</span>
            {r.text || ' '}
          </div>
        ))}
        {rows.length > shown.length ? (
          <div className="px-3 py-1 text-ink-3">… {rows.length - shown.length} more lines</div>
        ) : null}
      </div>
    </div>
  );
}
