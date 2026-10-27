'use client';

/**
 * Improve: learn a change to an agent from how its runs went wrong, prove it on saved runs,
 * and publish it only when it did better and a person approves.
 */

import Link from 'next/link';
import { useEffect, useState } from 'react';

import { AppShell, PageBody } from '@/components/layout/AppShell';
import { PROPOSAL_STATUS, ProposalStatusPill } from '@/components/traces/Proposal';
import { TracesNav } from '@/components/traces/TracesNav';
import { Button, Card, CardHeader, EmptyState, ErrorNote, PageHeader, Select, SkeletonRows } from '@/components/ui';
import { useDatasets, usePropose, useProposals } from '@/lib/api/hooks/useEvaluations';
import { useHarnesses } from '@/lib/api/hooks/useHarnesses';
import { formatStamp } from '@/lib/utils';

const STEPS = [
  ['Learn', 'A model reads the agent’s issues (failing checks, judges’ reasons, runs marked wrong) and proposes prompt edits.'],
  ['Measure', 'The change is replayed on a dataset of saved runs beside the live version. It never sees those runs while learning.'],
  ['Gate', 'It is refused unless it does better on some saved run and worse on none.'],
  ['Approve', 'One that passes waits in Approvals with its diff and results. Approving publishes it as the next version.'],
] as const;

export default function ImprovePage(): JSX.Element {
  const { data: agents } = useHarnesses();
  const published = (agents?.data ?? []).filter((a) => a.bind?.composition_id);
  const [agent, setAgent] = useState('');
  const [dataset, setDataset] = useState('');
  const [notes, setNotes] = useState('');
  useEffect(() => {
    if (!agent && published.length) setAgent(published[0].id);
  }, [agent, published]);
  const { data: datasets } = useDatasets(agent || undefined);
  const usable = (datasets ?? []).filter((d) => d.items > 0);
  useEffect(() => {
    if (usable.length && !usable.some((d) => d.id === dataset)) setDataset(usable[0].id);
  }, [usable, dataset]);
  const propose = usePropose(agent);
  const { data, isLoading, error, refetch } = useProposals({ agentId: agent || undefined });
  const working = data?.some((p) => p.status === 'drafting' || p.status === 'evaluating');

  return (
    <AppShell>
      <PageBody>
        <PageHeader
          title="Traces"
          description="Every run of every agent: what was asked, what it did, and where the time and money went."
        />
        <TracesNav current="/traces/improve" />
        <ol className="grid gap-3 md:grid-cols-4" aria-label="How a change is made">
          {STEPS.map(([title, text], n) => (
            <li key={title} className="flex flex-col gap-1 rounded-xl border border-line-soft bg-surface px-4 py-3">
              <span className="text-xs font-semibold text-ink-3">
                {n + 1}. {title}
              </span>
              <span className="text-[12.5px] text-ink-2">{text}</span>
            </li>
          ))}
        </ol>
        {!published.length ? (
          <Card>
            <EmptyState title="No published agents" description="Publish an agent and run it; its failures are what it learns from." />
          </Card>
        ) : (
          <>
            <Card>
              <CardHeader
                title="Propose a change"
                description="It is drafted, measured and gated in the background; this takes a few minutes per saved run."
              />
              <form
                className="flex flex-col gap-3 border-t border-line-soft px-5 py-4"
                onSubmit={(e) => {
                  e.preventDefault();
                  if (dataset) propose.mutate({ dataset_id: dataset, notes: notes.trim() || null }, { onSuccess: () => setNotes('') });
                }}
              >
                <div className="flex flex-wrap items-end gap-3">
                  <Select label="Agent" value={agent} onChange={setAgent} options={published.map((a) => ({ value: a.id, label: a.name }))} />
                  <Select
                    label="Measured on"
                    value={dataset}
                    onChange={setDataset}
                    options={usable.length ? usable.map((d) => ({ value: d.id, label: `${d.name} (${d.items})` })) : [{ value: '', label: 'No dataset with saved runs' }]}
                  />
                </div>
                <label className="flex flex-col gap-1.5 text-sm">
                  <span className="text-ink-2">What should it fix? (optional)</span>
                  <textarea
                    value={notes}
                    onChange={(e) => setNotes(e.target.value)}
                    maxLength={2000}
                    rows={2}
                    placeholder="e.g. it answers before running the tests"
                    className="rounded-lg border border-line-strong bg-surface px-3 py-2"
                  />
                </label>
                <div className="flex flex-wrap items-center gap-3">
                  <Button type="submit" variant="primary" disabled={!dataset || working || propose.isPending} data-testid="propose">
                    Learn from the traces
                  </Button>
                  {!usable.length ? (
                    <span className="text-sm text-ink-3">
                      Save runs to a <Link href="/traces/datasets" className="text-accent-600 hover:underline">dataset</Link> first: a change is measured on them.
                    </span>
                  ) : working ? (
                    <span className="text-sm text-ink-3">A change for this agent is being worked on.</span>
                  ) : null}
                </div>
              </form>
            </Card>

            <Card>
              <CardHeader title="Changes" />
              {error ? <ErrorNote message="Could not load the changes." onRetry={() => void refetch()} /> : null}
              {isLoading ? (
                <div className="p-4">
                  <SkeletonRows rows={3} />
                </div>
              ) : data?.length ? (
                <ul className="divide-y divide-line-soft border-t border-line-soft" data-testid="proposals">
                  {data.map((p) => (
                    <li key={p.id} className="flex flex-col gap-1 px-5 py-3.5" data-testid="proposal" data-status={p.status}>
                      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
                        <Link href={`/traces/improve/${p.id}`} className="font-medium hover:text-accent-600 hover:underline">
                          {p.agent_name} v{p.base_version} → {p.release_version ? `v${p.release_version}` : 'a change'}
                        </Link>
                        <ProposalStatusPill status={p.status} />
                        <span className="text-xs text-ink-3">on {p.dataset_name}</span>
                        <span className="flex-1" />
                        <span className="text-xs text-ink-3">{formatStamp(p.created_at)}</span>
                      </div>
                      <p className="line-clamp-2 text-[13px] text-ink-2">
                        {p.gate ? p.gate.reasons.join('; ') : p.error ?? p.rationale ?? PROPOSAL_STATUS[p.status]?.says}
                      </p>
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="border-t border-line-soft px-5 py-4 text-sm text-ink-2">No changes proposed yet.</p>
              )}
            </Card>
          </>
        )}
      </PageBody>
    </AppShell>
  );
}
