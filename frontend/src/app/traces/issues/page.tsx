'use client';

/**
 * Issues: the ways runs go wrong, grouped by agent and by what judged them (a check, a judge,
 * people), counted, with the versions they hit and the latest runs to start from.
 */

import Link from 'next/link';
import { useState } from 'react';

import { AppShell, PageBody } from '@/components/layout/AppShell';
import { TracesNav } from '@/components/traces/TracesNav';
import { Card, EmptyState, ErrorNote, PageHeader, Select, SkeletonRows } from '@/components/ui';
import { useHarnesses } from '@/lib/api/hooks/useHarnesses';
import { useIssues } from '@/lib/api/hooks/useTraces';
import type { Issue } from '@/lib/api/types/trace.types';
import { cn, formatRelativeTime } from '@/lib/utils';

const SOURCE_LABEL: Record<string, string> = { check: 'Check', judge: 'Judge', person: 'People' };

function IssueCard({ issue }: { issue: Issue }): JSX.Element {
  return (
    <Card as="article" className="flex flex-col gap-3 px-5 py-4" data-testid="issue" data-key={issue.key}>
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <span
          className={cn(
            'rounded-[5px] px-1.5 py-px text-[10.5px] font-semibold uppercase tracking-[0.04em]',
            issue.source === 'person' ? 'bg-amber-50 text-amber-900' : issue.source === 'judge' ? 'bg-violet-50 text-violet-800' : 'bg-red-50 text-red-800',
          )}
        >
          {SOURCE_LABEL[issue.source] ?? issue.source}
        </span>
        <h2 className="text-[15px] font-semibold" data-testid="issue-title">
          {issue.title}
        </h2>
        <span className="tabular text-sm text-ink-2" data-testid="issue-runs">
          {issue.runs} run{issue.runs === 1 ? '' : 's'}
        </span>
        <span className="flex-1" />
        <span className="text-xs text-ink-3">last seen {formatRelativeTime(new Date(issue.last_seen_ms))}</span>
      </div>
      <div className="flex flex-wrap gap-x-4 gap-y-1 text-[12.5px] text-ink-3">
        {issue.agent_name ? <span>{issue.agent_name}</span> : null}
        {issue.versions.length ? (
          <span>
            on{' '}
            {issue.versions
              .map((v) => `${v.version != null ? `v${v.version}` : 'an unpublished version'} (${v.runs})`)
              .join(', ')}
          </span>
        ) : null}
      </div>
      <ul className="flex flex-col divide-y divide-line-soft rounded-lg border border-line-soft">
        {issue.examples.map((e) => (
          <li key={`${e.session_id}-${e.run_id}`} className="flex flex-col gap-0.5 px-3 py-2">
            <Link href={`/traces/${e.session_id}/${e.run_id}`} className="truncate text-[13.5px] font-medium hover:text-accent-600 hover:underline">
              {e.input || 'Run'}
            </Link>
            {e.reason ? <span className="text-[12.5px] text-ink-2">{e.reason}</span> : null}
            {e.summary ? <span className="text-xs text-ink-3">{e.summary}</span> : null}
          </li>
        ))}
      </ul>
    </Card>
  );
}

export default function IssuesPage(): JSX.Element {
  const [agent, setAgent] = useState('');
  const { data: agents } = useHarnesses();
  const { data, isLoading, error, refetch } = useIssues(agent || undefined);
  const issues = data?.issues ?? [];
  return (
    <AppShell>
      <PageBody>
        <PageHeader
          title="Traces"
          description="Every run of every agent: what was asked, what it did, and where the time and money went."
        />
        <TracesNav current="/traces/issues" />
        <div className="flex flex-wrap items-center gap-3">
          <Select
            label="Agent"
            hideLabel
            value={agent}
            onChange={setAgent}
            options={[{ value: '', label: 'Every agent' }, ...(agents?.data ?? []).map((a) => ({ value: a.id, label: a.name }))]}
          />
          <p className="text-sm text-ink-2">
            Failures grouped by what judged them and what they are about, the ones hitting most runs first.
          </p>
        </div>
        {error ? <ErrorNote message="Could not load issues." onRetry={() => void refetch()} /> : null}
        {isLoading ? (
          <Card padded>
            <SkeletonRows rows={4} />
          </Card>
        ) : issues.length === 0 && !error ? (
          <Card>
            <EmptyState
              title="No issues"
              description="When a check or a judge fails a run, or someone marks one wrong, it is grouped here."
            />
          </Card>
        ) : (
          <div className="flex flex-col gap-3" data-testid="issues">
            {issues.map((i) => (
              <IssueCard key={i.key} issue={i} />
            ))}
          </div>
        )}
      </PageBody>
    </AppShell>
  );
}
