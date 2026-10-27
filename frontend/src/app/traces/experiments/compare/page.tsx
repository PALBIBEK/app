'use client';

/**
 * Two experiments of one dataset side by side: every saved run, how each version did on it,
 * and whether the second did better or worse. The question a version change has to answer.
 */

import Link from 'next/link';
import { useSearchParams } from 'next/navigation';
import { Suspense } from 'react';

import { AppShell, PageBody } from '@/components/layout/AppShell';
import { ExperimentStatsLine } from '@/components/traces/ExperimentStatsLine';
import { ResultCell } from '@/components/traces/ResultCell';
import { Card, ErrorNote, PageHeader, SkeletonRows } from '@/components/ui';
import { useComparison } from '@/lib/api/hooks/useEvaluations';
import { cn } from '@/lib/utils';

const CHANGE: Record<string, { label: string; className: string }> = {
  better: { label: 'better', className: 'bg-emerald-50 text-emerald-800' },
  worse: { label: 'worse', className: 'bg-red-50 text-red-800' },
  same: { label: 'same', className: 'bg-slate-100 text-slate-700' },
  unknown: { label: 'not finished', className: 'bg-slate-100 text-slate-600' },
};

function Comparison(): JSX.Element {
  const search = useSearchParams();
  const a = search.get('a') ?? undefined;
  const b = search.get('b') ?? undefined;
  const { data, isLoading, error, refetch } = useComparison(a, b);
  return (
    <AppShell>
      <PageBody>
        <nav aria-label="Breadcrumb" className="flex items-center gap-2 text-sm text-ink-3">
          <Link href="/traces/datasets" className="hover:text-ink">
            Datasets
          </Link>
          <span aria-hidden="true">/</span>
          {data ? (
            <Link href={`/traces/datasets/${data.a.dataset_id}`} className="hover:text-ink">
              Dataset
            </Link>
          ) : null}
          <span aria-hidden="true">/</span>
          <span className="text-ink">Compare</span>
        </nav>
        <PageHeader
          title={data ? `${data.a.label} against ${data.b.label}` : 'Compare'}
          description={
            data ? (
              <span data-testid="comparison-summary">
                {data.b.label} did better on {data.better} and worse on {data.worse} of {data.rows.length} saved runs.
              </span>
            ) : undefined
          }
        />
        {error ? <ErrorNote message="Could not compare those experiments." onRetry={() => void refetch()} /> : null}
        {isLoading ? (
          <Card padded>
            <SkeletonRows rows={4} />
          </Card>
        ) : data ? (
          <Card>
            <div className="hidden gap-x-6 border-b border-line-soft px-5 py-2.5 text-xs text-ink-3 md:grid md:grid-cols-[minmax(0,1.1fr)_minmax(0,1fr)_minmax(0,1fr)_88px]">
              <span>Saved run</span>
              <span>
                {data.a.label} · <ExperimentStatsLine experiment={data.a} />
              </span>
              <span>
                {data.b.label} · <ExperimentStatsLine experiment={data.b} />
              </span>
              <span className="text-right">Change</span>
            </div>
            <ol className="divide-y divide-line-soft" data-testid="comparison-rows">
              {data.rows.map((row) => {
                const ch = CHANGE[row.change] ?? CHANGE.unknown;
                return (
                  <li
                    key={row.item_id}
                    className="grid gap-x-6 gap-y-2 px-5 py-3.5 md:grid-cols-[minmax(0,1.1fr)_minmax(0,1fr)_minmax(0,1fr)_88px]"
                    data-testid="comparison-row"
                    data-change={row.change}
                  >
                    <p className="line-clamp-3 whitespace-pre-wrap text-[13.5px]">{row.input}</p>
                    <ResultCell result={row.a} />
                    <ResultCell result={row.b} />
                    <span className={cn('h-fit w-fit justify-self-start rounded-full px-2 py-px text-xs md:justify-self-end', ch.className)}>
                      {ch.label}
                    </span>
                  </li>
                );
              })}
            </ol>
          </Card>
        ) : null}
      </PageBody>
    </AppShell>
  );
}

export default function ComparePage(): JSX.Element {
  return (
    <Suspense fallback={null}>
      <Comparison />
    </Suspense>
  );
}
