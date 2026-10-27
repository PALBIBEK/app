'use client';

/**
 * Datasets: runs saved per agent to replay on later versions. A run that went wrong, saved
 * with what a good answer does, becomes the check that a fix really fixes it.
 */

import Link from 'next/link';
import { useEffect, useState } from 'react';

import { AppShell, PageBody } from '@/components/layout/AppShell';
import { TracesNav } from '@/components/traces/TracesNav';
import { Button, Card, EmptyState, ErrorNote, PageHeader, Select, SkeletonRows } from '@/components/ui';
import { useCreateDataset, useDatasets } from '@/lib/api/hooks/useEvaluations';
import { useHarnesses } from '@/lib/api/hooks/useHarnesses';
import { formatStamp } from '@/lib/utils';

export default function DatasetsPage(): JSX.Element {
  const { data: agents } = useHarnesses();
  const published = (agents?.data ?? []).filter((a) => a.bind?.composition_id);
  const [agent, setAgent] = useState('');
  useEffect(() => {
    if (!agent && published.length) setAgent(published[0].id);
  }, [agent, published]);
  const { data, isLoading, error, refetch } = useDatasets(agent || undefined);
  const create = useCreateDataset(agent);
  const [name, setName] = useState('');

  return (
    <AppShell>
      <PageBody>
        <PageHeader
          title="Traces"
          description="Every run of every agent: what was asked, what it did, and where the time and money went."
        />
        <TracesNav current="/traces/datasets" />
        <div className="flex flex-wrap items-center gap-3">
          <Select label="Agent" value={agent} onChange={setAgent} options={published.map((a) => ({ value: a.id, label: a.name }))} />
          <p className="max-w-2xl text-sm text-ink-2">
            Save runs from their trace page, then replay a dataset on any version of the agent to compare versions on the same asks.
          </p>
        </div>
        {!published.length ? (
          <Card>
            <EmptyState title="No published agents" description="Publish an agent, run it, and save runs here." />
          </Card>
        ) : (
          <Card>
            {error ? <ErrorNote message="Could not load datasets." onRetry={() => void refetch()} /> : null}
            {isLoading ? (
              <div className="p-4">
                <SkeletonRows rows={3} />
              </div>
            ) : (
              <ul className="divide-y divide-line-soft" data-testid="datasets">
                {(data ?? []).map((d) => (
                  <li key={d.id} className="flex flex-wrap items-center gap-x-4 gap-y-1 px-5 py-3.5">
                    <Link href={`/traces/datasets/${d.id}`} className="font-medium hover:text-accent-600 hover:underline" data-testid="dataset-link">
                      {d.name}
                    </Link>
                    <span className="text-sm text-ink-3">
                      {d.items} run{d.items === 1 ? '' : 's'} · {d.experiments} experiment{d.experiments === 1 ? '' : 's'}
                    </span>
                    {d.description ? <span className="text-sm text-ink-2">{d.description}</span> : null}
                    <span className="flex-1" />
                    <span className="text-xs text-ink-3">created {formatStamp(d.created_at)}</span>
                  </li>
                ))}
                <li className="px-5 py-3.5">
                  <form
                    className="flex flex-wrap items-center gap-2"
                    onSubmit={(e) => {
                      e.preventDefault();
                      if (name.trim()) create.mutate({ name: name.trim() }, { onSuccess: () => setName('') });
                    }}
                  >
                    <label className="sr-only" htmlFor="new-dataset">
                      New dataset name
                    </label>
                    <input
                      id="new-dataset"
                      value={name}
                      onChange={(e) => setName(e.target.value)}
                      maxLength={120}
                      placeholder="New dataset, e.g. regressions"
                      className="h-10 min-w-0 flex-[0_1_320px] rounded-lg border border-line-strong bg-surface px-3"
                    />
                    <Button type="submit" disabled={!name.trim() || create.isPending}>
                      Create
                    </Button>
                  </form>
                </li>
              </ul>
            )}
          </Card>
        )}
      </PageBody>
    </AppShell>
  );
}
