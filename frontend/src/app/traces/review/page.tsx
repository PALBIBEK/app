'use client';

/**
 * Review: the runs a person should mark, where the agent's automatic verdicts are least to be
 * trusted (a judge could not decide, judges disagree, a judge passed what a check failed).
 * Marking a run takes it off the list, and is what each judge's agreement is measured on.
 */

import Link from 'next/link';
import { useEffect, useState } from 'react';

import { AppShell, PageBody } from '@/components/layout/AppShell';
import { ReviewItem } from '@/components/traces/ReviewItem';
import { TracesNav } from '@/components/traces/TracesNav';
import { Card, EmptyState, ErrorNote, PageHeader, Select, SkeletonRows } from '@/components/ui';
import { useHarnesses } from '@/lib/api/hooks/useHarnesses';
import { useReviewQueue } from '@/lib/api/hooks/useTraces';

export default function ReviewPage(): JSX.Element {
  const { data: agents } = useHarnesses();
  const published = (agents?.data ?? []).filter((a) => a.bind?.composition_id);
  const [agent, setAgent] = useState('');
  useEffect(() => {
    if (!agent && published.length) setAgent(published[0].id);
  }, [agent, published]);
  const { data, isLoading, error, refetch } = useReviewQueue(agent || undefined);
  const runs = data?.runs ?? [];
  return (
    <AppShell>
      <PageBody>
        <PageHeader
          title="Traces"
          description="Every run of every agent: what was asked, what it did, and where the time and money went."
        />
        <TracesNav current="/traces/review" />
        <div className="flex flex-wrap items-center gap-3">
          <Select label="Agent" value={agent} onChange={setAgent} options={published.map((a) => ({ value: a.id, label: a.name }))} />
          <p className="max-w-2xl text-sm text-ink-2">
            Runs whose automatic verdicts are least to be trusted. Your marks take them off this list, and show on the{' '}
            <Link href="/traces/judges" className="text-accent-600 hover:underline">
              judges
            </Link>{' '}
            how often each agrees with you.
          </p>
        </div>
        {error ? <ErrorNote message="Could not load the runs to review." onRetry={() => void refetch()} /> : null}
        {isLoading ? (
          <Card padded>
            <SkeletonRows rows={3} />
          </Card>
        ) : !runs.length && !error ? (
          <Card>
            <EmptyState title="Nothing to review" description="When a judge cannot decide, or verdicts disagree, the run waits here for you." />
          </Card>
        ) : (
          <Card>
            <ul className="divide-y divide-line-soft" data-testid="review-queue">
              {runs.map((r) => (
                <ReviewItem key={`${r.session_id}-${r.run_id}`} run={r} />
              ))}
            </ul>
          </Card>
        )}
      </PageBody>
    </AppShell>
  );
}
