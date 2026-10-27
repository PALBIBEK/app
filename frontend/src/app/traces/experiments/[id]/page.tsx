'use client';

/** One experiment: a dataset replayed on one version, item by item, as it goes. */

import Link from 'next/link';
import { useParams } from 'next/navigation';

import { AppShell, PageBody } from '@/components/layout/AppShell';
import { ExperimentStatsLine } from '@/components/traces/ExperimentStatsLine';
import { ResultCell } from '@/components/traces/ResultCell';
import { Button, Card, ErrorNote, PageHeader, SkeletonRows } from '@/components/ui';
import { useCancelExperiment, useExperiment } from '@/lib/api/hooks/useEvaluations';

export default function ExperimentPage(): JSX.Element {
  const params = useParams();
  const id = typeof params.id === 'string' ? params.id : '';
  const { data, isLoading, error, refetch } = useExperiment(id);
  const cancel = useCancelExperiment(id);
  const exp = data?.experiment;
  const going = exp?.status === 'queued' || exp?.status === 'running';
  const done = data?.results.filter((r) => !['pending', 'running'].includes(r.status)).length ?? 0;

  return (
    <AppShell>
      <PageBody>
        <nav aria-label="Breadcrumb" className="flex items-center gap-2 text-sm text-ink-3">
          <Link href="/traces/datasets" className="hover:text-ink">
            Datasets
          </Link>
          <span aria-hidden="true">/</span>
          {exp ? (
            <Link href={`/traces/datasets/${exp.dataset_id}`} className="hover:text-ink">
              {data?.dataset_name}
            </Link>
          ) : null}
          <span aria-hidden="true">/</span>
          <span className="text-ink">{exp?.label ?? 'Experiment'}</span>
        </nav>
        <PageHeader
          title={exp ? `${data?.dataset_name} on ${exp.label}` : 'Experiment'}
          description={
            exp ? (
              <span data-testid="experiment-progress">
                {going ? `Replaying: ${done} of ${data?.results.length} done.` : exp.status === 'done' ? 'Done. ' : `${exp.status}. `}
                {exp.error ? ` ${exp.error}` : ''} {exp.stats ? <ExperimentStatsLine experiment={exp} /> : null}
              </span>
            ) : undefined
          }
          actions={going ? <Button onClick={() => cancel.mutate()}>Stop</Button> : undefined}
        />
        {error ? <ErrorNote message="Could not load the experiment." onRetry={() => void refetch()} /> : null}
        {isLoading ? (
          <Card padded>
            <SkeletonRows rows={4} />
          </Card>
        ) : (
          <Card>
            <ol className="divide-y divide-line-soft" data-testid="experiment-results">
              {data?.results.map((r) => (
                <li key={r.item_id} className="grid gap-x-6 gap-y-2 px-5 py-3.5 md:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
                  <div className="flex min-w-0 gap-3">
                    <span className="tabular w-6 flex-none text-right font-mono text-sm text-ink-3">{r.position + 1}</span>
                    <div className="flex min-w-0 flex-col gap-1">
                      <p className="line-clamp-3 whitespace-pre-wrap">{r.input}</p>
                      {r.expected ? <p className="text-[12.5px] text-ink-3">Good answer: {r.expected}</p> : null}
                    </div>
                  </div>
                  <ResultCell result={r} />
                </li>
              ))}
            </ol>
          </Card>
        )}
      </PageBody>
    </AppShell>
  );
}
