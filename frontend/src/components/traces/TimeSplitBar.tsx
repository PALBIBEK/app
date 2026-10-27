'use client';

/** Where a run's time went: model, tools, waiting on you, other, as one bar (and a key). */

import type { TimeSplit } from '@/lib/api/types/trace.types';
import { formatMs, splitParts } from '@/lib/traces/format';
import { cn } from '@/lib/utils';

export function TimeSplitBar({
  time,
  legend = true,
  className,
}: {
  time: Partial<TimeSplit> | null | undefined;
  legend?: boolean;
  className?: string;
}): JSX.Element | null {
  const parts = splitParts(time);
  if (parts.length === 0) return null;
  const label = parts.map((p) => `${p.label} ${formatMs(p.ms)}`).join(', ');
  return (
    <div className={cn('flex min-w-0 flex-col gap-1.5', className)}>
      <div role="img" aria-label={label} className="flex h-2.5 overflow-hidden rounded-full bg-line-soft">
        {parts.map((p) => (
          <div key={p.key} className={p.className} style={{ width: `${p.pct}%` }} />
        ))}
      </div>
      {legend ? (
        <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-ink-2" aria-hidden="true">
          {parts.map((p) => (
            <span key={p.key} className="inline-flex items-center gap-1.5">
              <span className={cn('h-2.5 w-2.5 rounded-[3px]', p.className)} />
              {p.label} <span className="tabular text-ink">{formatMs(p.ms)}</span>
            </span>
          ))}
        </div>
      ) : null}
    </div>
  );
}
