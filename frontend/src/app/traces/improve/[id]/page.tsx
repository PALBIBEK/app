'use client';

/**
 * One proposed change: why (the evidence it learned from), what (the prompt diff), and
 * whether it is better (the gate, and both replays). Approve it to publish it.
 */

import Link from 'next/link';
import { useParams } from 'next/navigation';

import { AppShell, PageBody } from '@/components/layout/AppShell';
import { ExperimentStatsLine } from '@/components/traces/ExperimentStatsLine';
import { GateResult, PROPOSAL_STATUS, PromptDiff, ProposalStatusPill } from '@/components/traces/Proposal';
import { Button, Card, CardHeader, ErrorNote, LinkButton, PageHeader, SkeletonRows } from '@/components/ui';
import { useDecideProposal, useProposal } from '@/lib/api/hooks/useEvaluations';

export default function ProposalPage(): JSX.Element {
  const params = useParams();
  const id = typeof params.id === 'string' ? params.id : '';
  const { data: p, isLoading, error, refetch } = useProposal(id);
  const decide = useDecideProposal(id);
  const st = p ? PROPOSAL_STATUS[p.status] : null;
  const decidable = p && ['awaiting_approval', 'refused', 'no_change', 'failed'].includes(p.status);

  return (
    <AppShell>
      <PageBody>
        <nav aria-label="Breadcrumb" className="flex items-center gap-2 text-sm text-ink-3">
          <Link href="/traces/improve" className="hover:text-ink">
            Improve
          </Link>
          <span aria-hidden="true">/</span>
          <span className="text-ink">{p ? `${p.agent_name} v${p.base_version}` : 'Change'}</span>
        </nav>
        <PageHeader
          title={p ? `A change to ${p.agent_name} v${p.base_version}` : 'A change'}
          description={
            p ? (
              <span className="flex flex-wrap items-center gap-2" data-testid="proposal-status" data-status={p.status}>
                <ProposalStatusPill status={p.status} />
                <span>{p.status === 'approved' && p.release_version ? `Published as v${p.release_version}.` : st?.says}</span>
                {p.error ? <span className="text-bad">{p.error}</span> : null}
              </span>
            ) : undefined
          }
          actions={
            decidable ? (
              <div className="flex gap-2">
                {p.status === 'awaiting_approval' ? (
                  <Button variant="primary" disabled={decide.isPending} onClick={() => decide.mutate('approve')} data-testid="approve">
                    Approve and publish
                  </Button>
                ) : null}
                <Button disabled={decide.isPending} onClick={() => decide.mutate('reject')}>
                  {p.status === 'awaiting_approval' ? 'Reject' : 'Dismiss'}
                </Button>
              </div>
            ) : undefined
          }
        />
        {error ? <ErrorNote message="Could not load the change." onRetry={() => void refetch()} /> : null}
        {isLoading || !p ? (
          <Card padded>
            <SkeletonRows rows={5} />
          </Card>
        ) : (
          <>
            {p.gate ? (
              <Card>
                <CardHeader
                  title="Is it better?"
                  description={`Each run saved in ${p.dataset_name}, replayed on v${p.base_version} and on the change.`}
                  actions={
                    p.baseline && p.candidate ? (
                      <LinkButton href={`/traces/experiments/compare?a=${p.baseline.id}&b=${p.candidate.id}`} size="sm">
                        Compare run by run
                      </LinkButton>
                    ) : undefined
                  }
                />
                <div className="flex flex-col gap-3 border-t border-line-soft px-5 py-4">
                  <GateResult gate={p.gate} />
                  {p.baseline ? (
                    <p className="text-[13px] text-ink-2">
                      <Link href={`/traces/experiments/${p.baseline.id}`} className="font-mono hover:underline">
                        v{p.base_version}
                      </Link>{' '}
                      · <ExperimentStatsLine experiment={p.baseline} />
                    </p>
                  ) : null}
                  {p.candidate ? (
                    <p className="text-[13px] text-ink-2">
                      <Link href={`/traces/experiments/${p.candidate.id}`} className="font-mono hover:underline">
                        the change
                      </Link>{' '}
                      · <ExperimentStatsLine experiment={p.candidate} />
                    </p>
                  ) : null}
                </div>
              </Card>
            ) : null}

            {p.rationale || p.candidate_prompt ? (
              <Card>
                <CardHeader title="What it changes" description={p.rationale ?? undefined} />
                <div className="border-t border-line-soft px-5 py-4">
                  {p.candidate_prompt ? (
                    <PromptDiff base={p.base_prompt} next={p.candidate_prompt} />
                  ) : (
                    <p className="text-sm text-ink-2">No change to the prompt.</p>
                  )}
                </div>
              </Card>
            ) : null}

            {p.evidence.length || p.notes ? (
              <Card>
                <CardHeader title="What it learned from" description="The agent's issues from real runs. Replays never count as evidence." />
                <div className="flex flex-col gap-3 border-t border-line-soft px-5 py-4">
                  {p.notes ? <p className="text-[13.5px]">You asked: “{p.notes}”</p> : null}
                  {p.evidence.map((e) => (
                    <div key={e.title} className="flex flex-col gap-1.5" data-testid="evidence">
                      <p className="text-[13.5px] font-medium">
                        {e.title} <span className="font-normal text-ink-3">· {e.runs} run{e.runs === 1 ? '' : 's'}</span>
                      </p>
                      <ul className="flex flex-col divide-y divide-line-soft rounded-lg border border-line-soft">
                        {e.examples.map((x, n) => (
                          <li key={n} className="flex flex-col gap-0.5 px-3 py-2 text-[12.5px]">
                            {x.session_id && x.run_id ? (
                              <Link href={`/traces/${x.session_id}/${x.run_id}`} className="font-medium hover:text-accent-600 hover:underline">
                                {x.input || 'Run'}
                              </Link>
                            ) : (
                              <span className="font-medium">{x.input || 'Run'}</span>
                            )}
                            {x.reason ? <span className="text-ink-2">{x.reason}</span> : null}
                            {x.summary ? <span className="text-ink-3">{x.summary}</span> : null}
                          </li>
                        ))}
                      </ul>
                    </div>
                  ))}
                </div>
              </Card>
            ) : null}
          </>
        )}
      </PageBody>
    </AppShell>
  );
}
