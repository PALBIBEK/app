'use client';

/** One replayed item: how it went, its summary, its verdicts, and a link to its trace. */

import Link from 'next/link';

import type { ExperimentItemResult } from '@/lib/api/types/evaluation.types';
import { runStatus } from '@/lib/traces/format';
import { cn } from '@/lib/utils';

const REPLAY: Record<string, string> = {
  pending: 'waiting',
  running: 'replaying…',
  needs_approval: 'stopped for an approval',
  timed_out: 'timed out',
  failed: 'could not replay',
};

export function ResultCell({ result }: { result: ExperimentItemResult | null }): JSX.Element {
  if (!result) return <span className="text-sm text-ink-3">—</span>;
  const ended = result.run_status ? runStatus(result.run_status) : null;
  const headline = result.status === 'done' && ended ? ended.label : REPLAY[result.status] ?? result.status;
  const bad = result.status !== 'done' || (ended && ended.kind === 'failed');
  return (
    <div className="flex min-w-0 flex-col gap-1" data-testid="result" data-status={result.status} data-run-status={result.run_status ?? ''}>
      <div className="flex flex-wrap items-center gap-2 text-[13px]">
        <span className={cn('font-medium', bad ? 'text-bad' : 'text-ink')}>{headline}</span>
        {result.session_id && result.run_id ? (
          <Link href={`/traces/${result.session_id}/${result.run_id}`} className="text-xs text-accent-600 hover:underline">
            trace
          </Link>
        ) : null}
      </div>
      {result.summary ? <span className="text-[12.5px] text-ink-2">{result.summary}</span> : null}
      {result.error && result.status !== 'done' ? <span className="text-[12.5px] text-ink-3">{result.error}</span> : null}
      {result.verdicts.length ? (
        <span className="flex flex-wrap gap-1">
          {result.verdicts.map((v) => (
            <span
              key={`${v.source}-${v.name}`}
              title={v.reason ?? undefined}
              className={cn(
                'rounded-full px-1.5 py-px text-[11px]',
                v.label === 'pass' ? 'bg-emerald-50 text-emerald-800' : v.label === 'fail' ? 'bg-red-50 text-red-800' : 'bg-slate-100 text-slate-700',
              )}
            >
              {v.source === 'judge' ? `judge ${v.name}` : v.name.replace(/_/g, ' ')}: {v.label}
            </span>
          ))}
        </span>
      ) : null}
    </div>
  );
}
