'use client';

/**
 * A session replayed run by run: each turn as a numbered card (what was asked, how it ended,
 * its time, cost and tools) that opens the run's trace.
 */

import Link from 'next/link';
import { useParams } from 'next/navigation';

import { AppShell, PageBody } from '@/components/layout/AppShell';
import { TimeSplitBar } from '@/components/traces/TimeSplitBar';
import { Card, EmptyState, ErrorNote, PageHeader, SkeletonRows, StatusPill } from '@/components/ui';
import { useSessionTraces } from '@/lib/api/hooks/useTraces';
import type { RunSummary } from '@/lib/api/types/trace.types';
import { formatMs, runStatus, toolsLine } from '@/lib/traces/format';
import { cn, formatMoney, formatStamp } from '@/lib/utils';

function TurnCard({ run, n }: { run: RunSummary; n: number }): JSX.Element {
  const st = runStatus(run.status);
  return (
    <li>
      <Link
        href={`/traces/${run.session_id}/${run.run_id}`}
        className="flex gap-4 rounded-card border border-line bg-surface px-4 py-3.5 hover:border-accent-200 hover:bg-accent-50/30"
        data-testid="turn-card"
      >
        <span className="tabular w-6 flex-none pt-0.5 text-right font-mono text-sm text-ink-3">{n}</span>
        <span className="flex min-w-0 flex-1 flex-col gap-1.5">
          <span className="flex flex-wrap items-center gap-x-3 gap-y-1">
            <span className="min-w-0 truncate font-medium">{run.input || 'Run'}</span>
            <StatusPill kind={st.kind} label={st.label} />
          </span>
          {run.reason || run.output ? (
            <span className={cn('line-clamp-2 text-[13.5px]', st.kind === 'failed' ? 'text-bad' : 'text-ink-2')}>
              {run.reason ?? run.output}
            </span>
          ) : null}
          <span className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-ink-3">
            <span>{formatStamp(run.started_ms)}</span>
            <span className="tabular">{run.duration_ms != null ? formatMs(run.duration_ms) : 'going'}</span>
            <span className="tabular">{formatMoney(run.cost)}</span>
            {run.tool_calls ? <span>{toolsLine(run.tools)}</span> : null}
            {run.denials ? <span>{run.denials} denied</span> : null}
          </span>
          <TimeSplitBar time={run.time} legend={false} className="max-w-[360px]" />
        </span>
      </Link>
    </li>
  );
}

export default function SessionTracePage(): JSX.Element {
  const params = useParams();
  const id = typeof params.id === 'string' ? params.id : '';
  const { data, isLoading, error, refetch } = useSessionTraces(id);
  const total = data?.items.reduce((sum, r) => sum + r.cost, 0) ?? 0;

  return (
    <AppShell>
      <PageBody className="max-w-[960px]">
        <nav aria-label="Breadcrumb" className="flex items-center gap-2 text-sm text-ink-3">
          <Link href="/traces" className="hover:text-ink">
            Traces
          </Link>
          <span aria-hidden="true">/</span>
          <span className="text-ink">Session</span>
        </nav>
        <PageHeader
          title={data?.title || data?.items[0]?.input || 'Session'}
          description={
            data ? (
              <>
                {data.agent_name ?? 'Agent'}
                {data.version != null ? ` v${data.version}` : ''} · {data.items.length} run{data.items.length === 1 ? '' : 's'} ·{' '}
                {formatMoney(total)} ·{' '}
                <Link href={`/sessions/${id}`} className="text-accent-600 hover:underline">
                  open session
                </Link>
              </>
            ) : undefined
          }
        />
        {error ? <ErrorNote message="Could not load this session's runs." onRetry={() => void refetch()} /> : null}
        {isLoading ? (
          <Card padded>
            <SkeletonRows rows={4} />
          </Card>
        ) : data && data.items.length === 0 ? (
          <Card>
            <EmptyState title="No runs yet" description="Each message sent in this session starts a run." />
          </Card>
        ) : (
          <ol className="flex flex-col gap-2.5" aria-label="Runs in this session">
            {data?.items.map((run, i) => <TurnCard key={run.run_id} run={run} n={i + 1} />)}
          </ol>
        )}
      </PageBody>
    </AppShell>
  );
}
