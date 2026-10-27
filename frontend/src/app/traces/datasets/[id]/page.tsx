'use client';

/**
 * A dataset: the runs saved in it, and the experiments that replayed them on versions of the
 * agent. Run it on a version; pick two experiments to compare them item by item.
 */

import Link from 'next/link';
import { useParams } from 'next/navigation';
import { useState } from 'react';

import { AppShell, PageBody } from '@/components/layout/AppShell';
import { ExperimentStatsLine } from '@/components/traces/ExperimentStatsLine';
import { Button, Card, CardHeader, EmptyState, ErrorNote, LinkButton, PageHeader, Select, SkeletonRows, StatusPill, type StatusKind } from '@/components/ui';
import { useDataset, useDeleteItem, useStartExperiment } from '@/lib/api/hooks/useEvaluations';
import { useReleases } from '@/lib/api/hooks/useManifest';
import type { ExperimentStatus } from '@/lib/api/types/evaluation.types';
import { runStatus } from '@/lib/traces/format';
import { formatStamp } from '@/lib/utils';

const EXP_STATUS: Record<ExperimentStatus, { label: string; kind: StatusKind }> = {
  queued: { label: 'Waiting', kind: 'idle' },
  running: { label: 'Replaying', kind: 'running' },
  done: { label: 'Done', kind: 'done' },
  failed: { label: 'Failed', kind: 'failed' },
  cancelled: { label: 'Cancelled', kind: 'stopped' },
};

export default function DatasetPage(): JSX.Element {
  const params = useParams();
  const id = typeof params.id === 'string' ? params.id : '';
  const { data, isLoading, error, refetch } = useDataset(id);
  const { data: releases } = useReleases(data?.agent_id);
  const [version, setVersion] = useState('');
  const start = useStartExperiment(id);
  const remove = useDeleteItem(id);
  const [picked, setPicked] = useState<string[]>([]);
  const running = data?.experiments.some((e) => e.status === 'queued' || e.status === 'running');
  const toggle = (expId: string): void =>
    setPicked((p) => (p.includes(expId) ? p.filter((x) => x !== expId) : [...p.slice(-1), expId]));

  return (
    <AppShell>
      <PageBody>
        <nav aria-label="Breadcrumb" className="flex items-center gap-2 text-sm text-ink-3">
          <Link href="/traces/datasets" className="hover:text-ink">
            Datasets
          </Link>
          <span aria-hidden="true">/</span>
          <span className="text-ink">{data?.name ?? 'Dataset'}</span>
        </nav>
        <PageHeader
          title={data?.name ?? 'Dataset'}
          description={data ? `${data.agent_name ?? 'Agent'} · ${data.items.length} saved run${data.items.length === 1 ? '' : 's'}` : undefined}
        />
        {error ? <ErrorNote message="Could not load the dataset." onRetry={() => void refetch()} /> : null}
        {isLoading ? (
          <Card padded>
            <SkeletonRows rows={4} />
          </Card>
        ) : data ? (
          <>
            <Card>
              <CardHeader
                title="Experiments"
                description="Each replays every saved run on one version, in fresh sessions. Experiments never approve anything."
                actions={
                  <div className="flex flex-wrap items-center gap-2">
                    <Select
                      label="Version"
                      hideLabel
                      value={version}
                      onChange={setVersion}
                      options={[
                        { value: '', label: 'The live version' },
                        ...(releases ?? []).map((r) => ({ value: String(r.version), label: `v${r.version}` })),
                      ]}
                    />
                    <Button
                      variant="primary"
                      disabled={!data.items.length || running || start.isPending}
                      onClick={() => start.mutate(version ? Number(version) : null)}
                      data-testid="run-experiment"
                    >
                      Replay the dataset
                    </Button>
                  </div>
                }
              />
              {data.experiments.length ? (
                <ul className="divide-y divide-line-soft border-t border-line-soft" data-testid="experiments">
                  {data.experiments.map((e) => {
                    const st = EXP_STATUS[e.status] ?? EXP_STATUS.queued;
                    return (
                      <li key={e.id} className="flex flex-wrap items-center gap-x-4 gap-y-1 px-5 py-3" data-testid="experiment" data-status={e.status}>
                        <label className="flex items-center gap-2">
                          <input type="checkbox" checked={picked.includes(e.id)} onChange={() => toggle(e.id)} aria-label={`Pick ${e.label} to compare`} />
                          <Link href={`/traces/experiments/${e.id}`} className="font-mono text-[13px] font-medium hover:text-accent-600 hover:underline">
                            {e.label}
                          </Link>
                        </label>
                        <StatusPill kind={st.kind} label={st.label} />
                        <span className="text-[13px] text-ink-2">
                          <ExperimentStatsLine experiment={e} />
                        </span>
                        <span className="flex-1" />
                        <span className="text-xs text-ink-3">{formatStamp(e.created_at)}</span>
                      </li>
                    );
                  })}
                </ul>
              ) : (
                <p className="border-t border-line-soft px-5 py-4 text-sm text-ink-2">Not replayed yet.</p>
              )}
              {picked.length === 2 ? (
                <div className="border-t border-line-soft px-5 py-3">
                  <LinkButton href={`/traces/experiments/compare?a=${picked[0]}&b=${picked[1]}`} variant="primary">
                    Compare the two
                  </LinkButton>
                </div>
              ) : null}
            </Card>

            <Card>
              <CardHeader title="Saved runs" description="Save more from any run's trace page." />
              {data.items.length ? (
                <ol className="divide-y divide-line-soft border-t border-line-soft" data-testid="dataset-items">
                  {data.items.map((item, n) => (
                    <li key={item.id} className="flex gap-4 px-5 py-3">
                      <span className="tabular w-6 flex-none text-right font-mono text-sm text-ink-3">{n + 1}</span>
                      <div className="flex min-w-0 flex-1 flex-col gap-1">
                        <p className="line-clamp-2 whitespace-pre-wrap">{item.input}</p>
                        {item.expected ? <p className="text-[13px] text-ink-2">Good answer: {item.expected}</p> : null}
                        <div className="flex flex-wrap gap-x-3 text-xs text-ink-3">
                          {item.source_version != null ? <span>from v{item.source_version}</span> : null}
                          {item.source_status ? <span>{runStatus(item.source_status).label.toLowerCase()} then</span> : null}
                          {item.source_session_id && item.source_run_id ? (
                            <Link href={`/traces/${item.source_session_id}/${item.source_run_id}`} className="text-accent-600 hover:underline">
                              the saved run
                            </Link>
                          ) : null}
                        </div>
                      </div>
                      <Button size="sm" variant="ghost" onClick={() => remove.mutate(item.id)} aria-label={`Remove run ${n + 1}`}>
                        Remove
                      </Button>
                    </li>
                  ))}
                </ol>
              ) : (
                <EmptyState title="No runs saved yet" description="Open a run's trace and use “Save to a dataset”." />
              )}
            </Card>
          </>
        ) : null}
      </PageBody>
    </AppShell>
  );
}
