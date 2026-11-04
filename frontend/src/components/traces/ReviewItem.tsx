'use client';

/** One run waiting for a person's mark: why, its verdicts, and good or bad. */

import Link from 'next/link';

import { Button } from '@/components/ui';
import { useFeedback } from '@/lib/api/hooks/useTraces';
import type { ReviewRun } from '@/lib/api/types/trace.types';
import { cn } from '@/lib/utils';

export function ReviewItem({ run }: { run: ReviewRun }): JSX.Element {
  const mark = useFeedback(run.session_id, run.run_id);
  return (
    <li className="flex flex-col gap-1.5 px-5 py-4" data-testid="review-run" data-why={run.why}>
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <Link href={`/traces/${run.session_id}/${run.run_id}`} className="min-w-0 flex-1 truncate font-medium hover:underline">
          {run.input || 'Run'}
        </Link>
        <span className="text-xs font-medium text-amber-900">{run.says}</span>
      </div>
      {run.summary ? <p className="text-[13px] text-ink-2">{run.summary}</p> : null}
      <ul className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-ink-3">
        {run.verdicts.map((v) => (
          <li key={v.name}>
            <span className={cn('font-semibold', v.label === 'pass' ? 'text-emerald-800' : v.label === 'fail' ? 'text-bad' : 'text-ink-2')}>
              {v.name}: {v.label}
            </span>
            {v.reason ? ` — ${v.reason}` : ''}
          </li>
        ))}
        {run.checks_failed.length ? <li className="text-bad">failed checks: {run.checks_failed.join(', ')}</li> : null}
      </ul>
      <div className="flex gap-2">
        <Button size="sm" disabled={mark.isPending} onClick={() => mark.mutate({ good: true })} data-testid="mark-good">
          Good
        </Button>
        <Button size="sm" disabled={mark.isPending} onClick={() => mark.mutate({ good: false })} data-testid="mark-bad">
          Wrong
        </Button>
      </div>
    </li>
  );
}
