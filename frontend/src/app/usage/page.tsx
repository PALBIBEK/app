/**
 * Usage: tokens and spend of every model call in a window, by day, agent and session.
 */

'use client';

import Link from 'next/link';
import { useMemo, useState } from 'react';

import { AppShell, PageBody } from '@/components/layout/AppShell';
import { Card, CardHeader, EmptyState, ErrorNote, Meter, PageHeader, Segmented, SkeletonRows, StatGrid, StatTile, Table, Td, Th, Tr } from '@/components/ui';
import { getApiErrorMessage } from '@/lib/api/errors';
import { useUsage } from '@/lib/api/hooks/useOperate';
import type { UsageRow } from '@/lib/api/types/operate.types';
import { formatCount, formatMoney } from '@/lib/utils';

const DAY = 86_400_000;
type Window = '7' | '30' | '90';

function dayKey(ms: number): string {
  return new Date(ms).toISOString().slice(0, 10);
}

/** One bar per day of the window (days without calls are zero), spend on the y axis. */
function DailyBars({ rows, days, until }: { rows: UsageRow[]; days: number; until: number }): JSX.Element {
  const byDay = new Map(rows.map((r) => [r.key, r]));
  const series = Array.from({ length: days }, (_, i) => {
    const k = dayKey(until - (days - 1 - i) * DAY);
    return { day: k, row: byDay.get(k) };
  });
  const max = Math.max(...series.map((s) => s.row?.cost ?? 0), 0.0001);
  return (
    <div>
      <div className="flex h-40 items-end gap-[2px]" role="img" aria-label={`Spend per day over the last ${days} days`}>
        {series.map((s) => {
          const h = ((s.row?.cost ?? 0) / max) * 100;
          return (
            <div key={s.day} className="group relative flex h-full flex-1 items-end">
              <div className="w-full rounded-t-[2px] bg-ink transition-colors group-hover:bg-accent-500" style={{ height: `${Math.max(h, s.row ? 2 : 0)}%` }} />
              <div className="pointer-events-none absolute bottom-full left-1/2 z-10 mb-1 hidden -translate-x-1/2 whitespace-nowrap rounded-md bg-ink px-2 py-1 font-mono text-[11px] text-white group-hover:block">
                {s.day} · {formatMoney(s.row?.cost ?? 0)} · {formatCount(s.row?.total_tokens ?? 0)} tok
              </div>
            </div>
          );
        })}
      </div>
      <div className="mt-1.5 flex justify-between font-mono text-[11px] text-ink-3">
        <span>{series[0]?.day.slice(5)}</span>
        <span>{series[series.length - 1]?.day.slice(5)}</span>
      </div>
    </div>
  );
}

function Breakdown({ title, rows, total, link }: { title: string; rows: UsageRow[]; total: number; link?: (key: string) => string | null }): JSX.Element {
  const sorted = [...rows].sort((a, b) => b.cost - a.cost);
  return (
    <Card>
      <CardHeader title={title} />
      {sorted.length === 0 ? (
        <p className="px-4 py-6 text-sm text-ink-2">No model calls in this window.</p>
      ) : (
        <Table minWidth={560} label={title}>
          <thead>
            <tr>
              <Th>{title.replace('By ', '').replace(/^./, (c) => c.toUpperCase())}</Th>
              <Th align="right">Spend</Th>
              <Th>Share</Th>
              <Th align="right">Tokens</Th>
              <Th align="right">Calls</Th>
            </tr>
          </thead>
          <tbody>
            {sorted.slice(0, 15).map((r) => {
              const href = link?.(r.key);
              return (
                <Tr key={r.key}>
                  <Td>
                    {href ? (
                      <Link href={href} className="text-ink hover:text-accent-700">
                        {r.label}
                      </Link>
                    ) : (
                      <span className="text-ink">{r.label}</span>
                    )}
                  </Td>
                  <Td mono align="right">
                    {formatMoney(r.cost)}
                  </Td>
                  <Td className="w-[140px]">
                    <Meter value={r.cost} max={total || 1} label={`${r.label}'s share of spend`} warnAt={2} />
                  </Td>
                  <Td mono align="right">
                    {formatCount(r.total_tokens)}
                  </Td>
                  <Td mono align="right">
                    {r.model_calls}
                  </Td>
                </Tr>
              );
            })}
          </tbody>
        </Table>
      )}
    </Card>
  );
}

export default function UsagePage(): JSX.Element {
  const [win, setWin] = useState<Window>('30');
  const days = Number(win);
  // A stable window per selection, rounded to the minute so the queries share a key.
  const until = useMemo(() => Math.ceil(Date.now() / 60_000) * 60_000, []);
  const since = until - days * DAY;
  const byDay = useUsage('day', since, until);
  const byAgent = useUsage('agent', since, until);
  const bySession = useUsage('session', since, until);
  const total = byDay.data?.total;
  const error = byDay.error ?? byAgent.error ?? bySession.error;

  return (
    <AppShell>
      <PageBody>
        <PageHeader
          title="Usage"
          description="Tokens and spend of every model call your sessions made, from the session journals."
          actions={
            <Segmented
              label="Window"
              value={win}
              onChange={setWin}
              options={[
                { value: '7', label: '7 days' },
                { value: '30', label: '30 days' },
                { value: '90', label: '90 days' },
              ]}
            />
          }
        />
        {error ? <ErrorNote message={getApiErrorMessage(error, 'Could not load usage')} onRetry={() => void byDay.refetch()} /> : null}
        <StatGrid>
          <StatTile label="Spend" value={total ? formatMoney(total.cost) : '—'} hint={`Last ${days} days`} />
          <StatTile label="Tokens" value={total ? formatCount(total.total_tokens) : '—'} hint={total ? `${formatCount(total.input_tokens)} in · ${formatCount(total.output_tokens)} out` : undefined} />
          <StatTile label="Model calls" value={total ? total.model_calls.toLocaleString() : '—'} />
          <StatTile label="Sessions" value={total ? total.sessions.toLocaleString() : '—'} hint={total && total.sessions ? `${formatMoney(total.cost / total.sessions)} per session` : undefined} />
        </StatGrid>

        <Card padded>
          <div className="mb-4 flex items-baseline justify-between">
            <h2 className="text-base font-semibold">Spend per day</h2>
            <span className="text-[12.5px] text-ink-3">hover a bar for its day</span>
          </div>
          {byDay.isLoading ? (
            <SkeletonRows rows={3} />
          ) : byDay.data && byDay.data.total.model_calls === 0 ? (
            <EmptyState title="No model calls yet" description="Start a session: its calls show up here as they happen." />
          ) : byDay.data ? (
            <DailyBars rows={byDay.data.rows} days={days} until={until} />
          ) : null}
        </Card>

        <div className="grid gap-5 xl:grid-cols-2">
          {byAgent.isLoading ? <SkeletonRows rows={4} /> : <Breakdown title="By agent" rows={byAgent.data?.rows ?? []} total={total?.cost ?? 0} />}
          {bySession.isLoading ? (
            <SkeletonRows rows={4} />
          ) : (
            <Breakdown title="By session" rows={bySession.data?.rows ?? []} total={total?.cost ?? 0} link={(k) => `/sessions/${k}`} />
          )}
        </div>
      </PageBody>
    </AppShell>
  );
}
