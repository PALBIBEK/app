/**
 * Overview: what needs the user now (approvals), what is running, and what it has cost.
 */

'use client';

import Link from 'next/link';
import { useMemo } from 'react';
import { ShieldWarning } from '@phosphor-icons/react';

import { AppShell, PageBody } from '@/components/layout/AppShell';
import { AiPlatformBanner } from '@/components/platform/AiPlatformBanner';
import {
  Card,
  CardHeader,
  EmptyState,
  LinkButton,
  Mono,
  PageHeader,
  SkeletonRows,
  StatGrid,
  StatTile,
  StatusPill,
  sessionStatusKind,
} from '@/components/ui';
import { useHarnesses } from '@/lib/api/hooks/useHarnesses';
import { useApprovalInbox, useSandboxes, useUsage } from '@/lib/api/hooks/useOperate';
import { useSessions } from '@/lib/api/hooks/useSessions';
import { useAuthStore } from '@/lib/store/authStore';
import { formatCount, formatDuration, formatMoney, formatRelativeTime } from '@/lib/utils';

const DAY = 86_400_000;

export default function OverviewPage(): JSX.Element {
  const user = useAuthStore((s) => s.user);
  const until = useMemo(() => Math.ceil(Date.now() / 60_000) * 60_000, []);
  const { data: inbox = [] } = useApprovalInbox();
  const { data: sessions, isLoading } = useSessions({ page: 1, page_size: 50 });
  const { data: harnesses } = useHarnesses();
  const { data: boxes = [] } = useSandboxes();
  const { data: week } = useUsage('day', until - 7 * DAY, until);

  const names = useMemo(() => new Map((harnesses?.data ?? []).map((h) => [h.id, h.name])), [harnesses]);
  const waiting = useMemo(() => new Set(inbox.map((i) => i.session_id)), [inbox]);
  const list = sessions?.data ?? [];
  const running = list.filter((s) => s.is_busy || waiting.has(s.id));
  const recent = list.slice(0, 6);
  const live = boxes.filter((b) => b.state === 'running');
  const now = Date.now();

  const days = Array.from({ length: 7 }, (_, i) => new Date(until - (6 - i) * DAY).toISOString().slice(0, 10));
  const byDay = new Map((week?.rows ?? []).map((r) => [r.key, r]));
  const max = Math.max(...days.map((d) => byDay.get(d)?.cost ?? 0), 0.0001);

  return (
    <AppShell>
      <PageBody>
        <AiPlatformBanner />
        <PageHeader
          title={user?.name ? `Hello, ${user.name.split(' ')[0]}` : 'Overview'}
          description="What needs you, what is running, and what it has cost this week."
          actions={
            <LinkButton href="/" variant="primary">
              Start a session
            </LinkButton>
          }
        />
        <StatGrid>
          <StatTile label="Awaiting your approval" value={inbox.length} tone={inbox.length ? 'accent' : 'default'} />
          <StatTile label="Running now" value={running.length} />
          <StatTile label="Sandboxes running" value={live.length} />
          <StatTile label="Spend · 7 days" value={week ? formatMoney(week.total.cost) : '—'} hint={week ? `${formatCount(week.total.total_tokens)} tokens · ${week.total.model_calls} calls` : undefined} />
        </StatGrid>

        {inbox.length ? (
          <Card tone="accent" aria-label="Needs your approval">
            <CardHeader
              tone="accent"
              title="Needs your approval"
              icon={<ShieldWarning size={16} />}
              actions={
                <Link href="/approvals" className="text-[13px] font-medium text-accent-700">
                  All {inbox.length}
                </Link>
              }
            />
            <ul className="divide-y divide-line-soft">
              {inbox.slice(0, 3).map((i) => (
                <li key={i.approval_id} className="flex flex-wrap items-center gap-x-4 gap-y-2 px-4 py-3">
                  <span className="font-medium">{i.session_title || `${i.harness_name ?? 'Agent'} session`}</span>
                  <Mono chip className="max-w-[420px] truncate">
                    {i.prompt}
                  </Mono>
                  <span className="flex-1" />
                  <Mono className="text-xs text-ink-3">waiting {i.created_at_ms ? formatDuration(now - i.created_at_ms) : ''}</Mono>
                  <LinkButton href={`/sessions/${i.session_id}`} size="sm" variant="primary">
                    Review
                  </LinkButton>
                </li>
              ))}
            </ul>
          </Card>
        ) : null}

        <div className="grid gap-5 xl:grid-cols-[1.4fr_1fr]">
          <Card aria-label="Recent sessions">
            <CardHeader
              title="Recent sessions"
              actions={
                <Link href="/sessions" className="text-[13px] text-ink-2 hover:text-ink">
                  All sessions
                </Link>
              }
            />
            {isLoading ? (
              <SkeletonRows rows={4} />
            ) : recent.length === 0 ? (
              <EmptyState title="No sessions yet" description="Pick an agent and start one." action={<LinkButton href="/">Agents</LinkButton>} />
            ) : (
              <ul className="divide-y divide-line-soft">
                {recent.map((s) => (
                  <li key={s.id} className="flex flex-wrap items-center gap-x-4 gap-y-1 px-4 py-3">
                    <Link href={`/sessions/${s.id}`} className="min-w-0 flex-1 truncate font-medium text-ink hover:text-accent-700">
                      {s.title || `${names.get(s.harness_id) ?? 'Agent'} session`}
                    </Link>
                    <StatusPill kind={waiting.has(s.id) ? 'awaiting' : sessionStatusKind(s.status, s.is_busy)} />
                    <Mono className="w-16 text-right text-ink-2">{formatMoney(s.total_cost)}</Mono>
                    <span className="w-20 text-right text-[13px] text-ink-3">{formatRelativeTime(s.updated_at)}</span>
                  </li>
                ))}
              </ul>
            )}
          </Card>

          <div className="flex flex-col gap-5">
            <Card padded aria-label="Spend this week">
              <div className="mb-3 flex items-baseline justify-between">
                <h2 className="text-base font-semibold">Spend this week</h2>
                <Link href="/usage" className="text-[13px] text-ink-2 hover:text-ink">
                  Usage
                </Link>
              </div>
              <div className="flex h-24 items-end gap-1.5" role="img" aria-label="Spend per day, last 7 days">
                {days.map((d) => {
                  const c = byDay.get(d)?.cost ?? 0;
                  return (
                    <div key={d} className="flex h-full flex-1 flex-col items-center justify-end gap-1" title={`${d}: ${formatMoney(c)}`}>
                      <div className="w-full rounded-t-[2px] bg-ink" style={{ height: `${Math.max((c / max) * 100, c ? 3 : 0)}%` }} />
                    </div>
                  );
                })}
              </div>
              <div className="mt-1.5 flex gap-1.5 font-mono text-[11px] text-ink-3">
                {days.map((d) => (
                  <span key={d} className="flex-1 text-center">
                    {new Date(`${d}T00:00:00Z`).toLocaleDateString('en-US', { weekday: 'narrow', timeZone: 'UTC' })}
                  </span>
                ))}
              </div>
            </Card>

            <Card aria-label="Sandboxes running">
              <CardHeader
                title="Sandboxes running"
                actions={
                  <Link href="/sandboxes" className="text-[13px] text-ink-2 hover:text-ink">
                    All sandboxes
                  </Link>
                }
              />
              {live.length === 0 ? (
                <p className="px-4 py-4 text-[13px] text-ink-2">None right now.</p>
              ) : (
                <ul className="divide-y divide-line-soft">
                  {live.slice(0, 5).map((b) => (
                    <li key={`${b.session_id}/${b.hand}`} className="flex items-center gap-3 px-4 py-2.5 text-[13px]">
                      <StatusPill kind="running" label={b.hand} />
                      <Link href={`/sessions/${b.session_id}`} className="min-w-0 flex-1 truncate text-ink hover:text-accent-700">
                        {b.session_title || `${b.harness_name ?? 'Agent'} session`}
                      </Link>
                      <Mono className="text-xs text-ink-3">{b.provider}</Mono>
                    </li>
                  ))}
                </ul>
              )}
            </Card>
          </div>
        </div>
      </PageBody>
    </AppShell>
  );
}
