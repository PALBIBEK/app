'use client';

/**
 * Traces: every run of every agent, newest first. A row says what was asked and how it
 * ended, which agent version ran it, where its time went and what it cost; filters find
 * the runs that failed, waited on you or were denied.
 */

import Link from 'next/link';
import { useEffect, useState } from 'react';

import { AppShell, PageBody } from '@/components/layout/AppShell';
import { TimeSplitBar } from '@/components/traces/TimeSplitBar';
import { TracesNav } from '@/components/traces/TracesNav';
import {
  Button,
  Card,
  EmptyState,
  ErrorNote,
  PageHeader,
  SearchInput,
  Segmented,
  Select,
  SkeletonRows,
  StatusPill,
  Table,
  Td,
  Th,
  Tr,
} from '@/components/ui';
import { useHarnesses } from '@/lib/api/hooks/useHarnesses';
import { useTraceList } from '@/lib/api/hooks/useTraces';
import type { RunStatus, RunSummary } from '@/lib/api/types/trace.types';
import { formatMs, runStatus, toolsLine } from '@/lib/traces/format';
import { cn, formatMoney, formatRelativeTime } from '@/lib/utils';

type View = 'all' | 'live' | 'failed' | 'answered' | 'stopped';

const VIEWS: Record<View, { label: string; statuses: RunStatus[] }> = {
  all: { label: 'All', statuses: [] },
  live: { label: 'Going now', statuses: ['running', 'awaiting_approval'] },
  failed: { label: 'Failed', statuses: ['error', 'budget_exceeded'] },
  answered: { label: 'Answered', statuses: ['ok'] },
  stopped: { label: 'Stopped', statuses: ['cancelled', 'interrupted'] },
};

function useDebounced<T>(value: T, ms: number): T {
  const [v, setV] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setV(value), ms);
    return () => clearTimeout(t);
  }, [value, ms]);
  return v;
}

function Row({ run }: { run: RunSummary }): JSX.Element {
  const st = runStatus(run.status);
  const href = `/traces/${run.session_id}/${run.run_id}`;
  const outcome = run.summary ?? run.reason ?? run.output;
  return (
    <Tr className="align-top">
      <Td className="max-w-[440px]">
        <Link href={href} className="block truncate font-medium hover:text-accent-600 hover:underline" data-testid="trace-row-link">
          {run.input || 'Run'}
        </Link>
        {outcome ? (
          <div className={cn('truncate text-[12.5px]', st.kind === 'failed' ? 'text-bad' : 'text-ink-3')}>{outcome}</div>
        ) : null}
      </Td>
      <Td className="whitespace-nowrap">
        {run.agent_name ?? '—'}
        {run.version != null ? (
          <span className="ml-1.5 rounded-md bg-accent-50 px-1.5 py-px font-mono text-xs text-accent-700">v{run.version}</span>
        ) : null}
      </Td>
      <Td className="whitespace-nowrap">
        <StatusPill kind={st.kind} label={st.label} />
      </Td>
      <Td className="w-[200px]">
        <div className="flex items-center gap-2">
          <TimeSplitBar time={run.time} legend={false} className="flex-1" />
          <span className="tabular w-[64px] text-right font-mono text-xs text-ink-2">
            {run.duration_ms != null ? formatMs(run.duration_ms) : 'going'}
          </span>
        </div>
      </Td>
      <Td align="right" className="tabular font-mono text-[12.5px]">
        {formatMoney(run.cost)}
      </Td>
      <Td className="max-w-[220px] truncate text-[12.5px] text-ink-2">
        {toolsLine(run.tools) || '—'}
        {run.tool_errors ? <span className="ml-1 text-bad">· {run.tool_errors} failed</span> : null}
        {run.denials ? <span className="ml-1">· {run.denials} denied</span> : null}
      </Td>
      <Td className="whitespace-nowrap text-[12.5px]" >
        <span data-testid="trace-row-checks" className={run.checks_failed ? 'text-bad' : 'text-ink-3'}>
          {run.checks_failed ? `${run.checks_failed} failed` : run.ended_ms != null ? 'passed' : '—'}
        </span>
        {run.feedback_bad ? <span className="ml-1.5 text-bad">· marked wrong</span> : null}
        {run.feedback_good && !run.feedback_bad ? <span className="ml-1.5 text-ink-3">· marked good</span> : null}
      </Td>
      <Td align="right" className="whitespace-nowrap text-[12.5px] text-ink-3">
        {formatRelativeTime(new Date(run.started_ms))}
      </Td>
    </Tr>
  );
}

export default function TracesPage(): JSX.Element {
  const [view, setView] = useState<View>('all');
  const [agent, setAgent] = useState('');
  const [errors, setErrors] = useState(false);
  const [denied, setDenied] = useState(false);
  const [failingChecks, setFailingChecks] = useState(false);
  const [markedWrong, setMarkedWrong] = useState(false);
  const [text, setText] = useState('');
  const q = useDebounced(text, 300);
  const { data: agents } = useHarnesses();
  const params = {
    status: VIEWS[view].statuses,
    agent_id: agent || undefined,
    errors,
    denied,
    failing_checks: failingChecks,
    feedback: markedWrong ? ('bad' as const) : undefined,
    q: q || undefined,
    limit: 50,
  };
  const { data, isLoading, error, refetch, fetchNextPage, hasNextPage, isFetchingNextPage } = useTraceList(params);
  const runs = data?.pages.flatMap((p) => p.items) ?? [];
  const filtered = view !== 'all' || !!agent || errors || denied || failingChecks || markedWrong || !!q;

  return (
    <AppShell>
      <PageBody>
        <PageHeader
          title="Traces"
          description="Every run of every agent: what was asked, what it did, and where the time and money went."
        />
        <TracesNav current="/traces" />
        <div className="flex flex-wrap items-center gap-2.5">
          <SearchInput value={text} onChange={setText} label="Search runs" placeholder="Search what was asked or answered" />
          <Segmented
            label="Run status"
            value={view}
            onChange={setView}
            options={(Object.keys(VIEWS) as View[]).map((v) => ({ value: v, label: VIEWS[v].label }))}
          />
          <Select
            label="Agent"
            hideLabel
            value={agent}
            onChange={setAgent}
            options={[{ value: '', label: 'Every agent' }, ...(agents?.data ?? []).map((a) => ({ value: a.id, label: a.name }))]}
          />
          <Button variant={errors ? 'primary' : 'secondary'} aria-pressed={errors} onClick={() => setErrors((v) => !v)}>
            With failures
          </Button>
          <Button variant={denied ? 'primary' : 'secondary'} aria-pressed={denied} onClick={() => setDenied((v) => !v)}>
            With denials
          </Button>
          <Button variant={failingChecks ? 'primary' : 'secondary'} aria-pressed={failingChecks} onClick={() => setFailingChecks((v) => !v)}>
            Failing checks
          </Button>
          <Button variant={markedWrong ? 'primary' : 'secondary'} aria-pressed={markedWrong} onClick={() => setMarkedWrong((v) => !v)}>
            Marked wrong
          </Button>
        </div>
        <Card>
          {error ? <ErrorNote message="Could not load runs." onRetry={() => void refetch()} /> : null}
          {isLoading ? (
            <div className="p-4">
              <SkeletonRows rows={6} />
            </div>
          ) : runs.length === 0 && !error ? (
            <EmptyState
              title={filtered ? 'No runs match' : 'No runs yet'}
              description={filtered ? 'Try another filter.' : 'Each message sent to an agent starts a run; its trace appears here.'}
            />
          ) : (
            <Table label="Runs" minWidth={1140}>
              <thead>
                <tr>
                  <Th>Run</Th>
                  <Th>Agent</Th>
                  <Th>Status</Th>
                  <Th>Time</Th>
                  <Th align="right">Cost</Th>
                  <Th>Tools</Th>
                  <Th>Checks</Th>
                  <Th align="right">Started</Th>
                </tr>
              </thead>
              <tbody data-testid="trace-rows">
                {runs.map((r) => (
                  <Row key={`${r.session_id}-${r.run_id}`} run={r} />
                ))}
              </tbody>
            </Table>
          )}
        </Card>
        {hasNextPage ? (
          <div className="flex justify-center">
            <Button onClick={() => void fetchNextPage()} disabled={isFetchingNextPage}>
              {isFetchingNextPage ? 'Loading…' : 'Older runs'}
            </Button>
          </div>
        ) : null}
      </PageBody>
    </AppShell>
  );
}
