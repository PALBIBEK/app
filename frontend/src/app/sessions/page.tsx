/**
 * Sessions: every run of an agent, live and past, with what waits on you on top.
 */

'use client';

import Link from 'next/link';
import { useCallback, useMemo, useState } from 'react';
import { ShieldWarning } from '@phosphor-icons/react';

import { AppShell, PageBody } from '@/components/layout/AppShell';
import {
  Button,
  Card,
  CardHeader,
  EmptyState,
  ErrorNote,
  LinkButton,
  Mono,
  PageHeader,
  SearchInput,
  Segmented,
  Select,
  SkeletonRows,
  StatGrid,
  StatTile,
  StatusPill,
  Table,
  Td,
  Th,
  Tr,
  sessionStatusKind,
  type StatusKind,
} from '@/components/ui';
import { useHarnesses } from '@/lib/api/hooks/useHarnesses';
import { useApprovalInbox } from '@/lib/api/hooks/useOperate';
import { useSessions } from '@/lib/api/hooks/useSessions';
import type { InboxItem } from '@/lib/api/types/operate.types';
import type { Session } from '@/lib/api/types/session.types';
import { formatDuration, formatMoney, formatRelativeTime, versionLabel } from '@/lib/utils';

const PAGE_SIZE = 25;

type Filter = 'all' | 'running' | 'awaiting' | 'done' | 'failed';

const FILTERS: Record<Filter, (k: StatusKind) => boolean> = {
  all: () => true,
  running: (k) => k === 'running' || k === 'idle',
  awaiting: (k) => k === 'awaiting',
  done: (k) => k === 'done' || k === 'stopped',
  failed: (k) => k === 'failed',
};

function isToday(iso: string): boolean {
  const d = new Date(iso);
  const now = new Date();
  return d.toDateString() === now.toDateString();
}

export default function SessionsPage(): JSX.Element {
  const [page, setPage] = useState(1);
  const [query, setQuery] = useState('');
  const [filter, setFilter] = useState<Filter>('all');
  const [agent, setAgent] = useState('all');

  const sessionsQ = useSessions({ page, page_size: PAGE_SIZE, ...(agent !== 'all' ? { harness_id: agent } : {}) });
  const harnessesQ = useHarnesses();
  const inboxQ = useApprovalInbox();

  const names = useMemo(() => {
    const m = new Map<string, string>();
    for (const h of harnessesQ.data?.data ?? []) m.set(h.id, h.name);
    return m;
  }, [harnessesQ.data]);

  const sessions = useMemo(() => sessionsQ.data?.data ?? [], [sessionsQ.data]);
  const inbox = useMemo(() => inboxQ.data ?? [], [inboxQ.data]);
  const waiting = useMemo(() => new Set(inbox.map((i) => i.session_id)), [inbox]);

  const kindOf = useCallback(
    (s: Session): StatusKind => (waiting.has(s.id) ? 'awaiting' : sessionStatusKind(s.status, s.is_busy)),
    [waiting],
  );

  const rows = useMemo(() => {
    const q = query.trim().toLowerCase();
    return sessions.filter((s) => {
      if (!FILTERS[filter](kindOf(s))) return false;
      if (!q) return true;
      return [s.id, s.title ?? '', names.get(s.harness_id) ?? '', s.harness_version].some((v) => v.toLowerCase().includes(q));
    });
  }, [sessions, query, filter, names, kindOf]);

  const counts = useMemo(() => {
    const c = { running: 0, awaiting: waiting.size, doneToday: 0, spendToday: 0 };
    for (const s of sessions) {
      const k = kindOf(s);
      if (k === 'running') c.running += 1;
      if ((k === 'done' || k === 'stopped') && isToday(s.updated_at)) c.doneToday += 1;
      if (isToday(s.updated_at)) c.spendToday += s.total_cost ?? 0;
    }
    return c;
  }, [sessions, waiting, kindOf]);

  const total = sessionsQ.data?.total ?? 0;
  const first = total === 0 ? 0 : (page - 1) * PAGE_SIZE + 1;
  const last = Math.min(page * PAGE_SIZE, total);

  const agentOptions = [
    { value: 'all', label: 'All agents' },
    ...(harnessesQ.data?.data ?? []).map((h) => ({ value: h.id, label: h.name })),
  ];

  return (
    <AppShell>
      <PageBody>
        <PageHeader
          title="Sessions"
          description="Every run of an agent version, live and past."
          actions={
            <LinkButton href="/" variant="primary">
              New session
            </LinkButton>
          }
        />

        <StatGrid>
          <StatTile label="Running" value={counts.running} />
          <StatTile label="Awaiting your approval" value={counts.awaiting} tone={counts.awaiting ? 'accent' : 'default'} />
          <StatTile label="Completed today" value={counts.doneToday} />
          <StatTile label="Spend today" value={formatMoney(counts.spendToday)} hint="Sessions on this page" />
        </StatGrid>

        {inbox.length > 0 ? <ApprovalStrip items={inbox} /> : null}

        <div className="flex flex-wrap items-center gap-x-4 gap-y-2.5">
          <SearchInput value={query} onChange={setQuery} label="Search sessions" placeholder="Search by title, id or agent" />
          <Segmented
            label="Filter by status"
            value={filter}
            onChange={setFilter}
            options={[
              { value: 'all', label: 'All' },
              { value: 'running', label: 'Running' },
              { value: 'awaiting', label: 'Awaiting approval' },
              { value: 'done', label: 'Completed' },
              { value: 'failed', label: 'Failed' },
            ]}
          />
          <span className="flex-1" />
          <Select
            label="Agent"
            value={agent}
            onChange={(v) => {
              setAgent(v);
              setPage(1);
            }}
            options={agentOptions}
          />
        </div>

        <Card aria-label="Session list">
          {sessionsQ.isLoading ? (
            <SkeletonRows rows={8} />
          ) : sessionsQ.error ? (
            <div className="p-4">
              <ErrorNote
                message={sessionsQ.error instanceof Error ? sessionsQ.error.message : 'Could not load sessions'}
                onRetry={() => void sessionsQ.refetch()}
              />
            </div>
          ) : rows.length === 0 ? (
            <EmptyState
              title={sessions.length === 0 ? 'No sessions yet' : 'Nothing matches'}
              description={
                sessions.length === 0
                  ? 'Start one from an agent: its runs, approvals and files will show up here.'
                  : 'Try another status or search.'
              }
              action={sessions.length === 0 ? <LinkButton href="/">Go to agents</LinkButton> : undefined}
            />
          ) : (
            <Table minWidth={900} label="Sessions">
              <thead>
                <tr>
                  <Th>Session</Th>
                  <Th>Agent</Th>
                  <Th>Status</Th>
                  <Th>Duration</Th>
                  <Th align="right">Messages</Th>
                  <Th align="right">Spend</Th>
                  <Th>Updated</Th>
                </tr>
              </thead>
              <tbody>
                {rows.map((s) => (
                  <Tr key={s.id}>
                    <Td>
                      <Link href={`/sessions/${s.id}`} className="font-medium text-ink hover:text-accent-700">
                        {s.title || `${names.get(s.harness_id) ?? 'Agent'} session`}
                      </Link>
                      <div>
                        <Mono className="text-xs text-ink-3">{s.id.slice(0, 13)}</Mono>
                      </div>
                    </Td>
                    <Td mono>
                      {names.get(s.harness_id) ?? 'agent'}
                      <span className="text-ink-3">@{versionLabel(s.harness_version)}</span>
                    </Td>
                    <Td>
                      <StatusPill kind={kindOf(s)} />
                    </Td>
                    <Td mono>{formatDuration(new Date(s.updated_at).getTime() - new Date(s.created_at).getTime())}</Td>
                    <Td mono align="right">
                      {s.message_count}
                    </Td>
                    <Td mono align="right">
                      {formatMoney(s.total_cost)}
                    </Td>
                    <Td className="whitespace-nowrap text-ink-2">{formatRelativeTime(s.updated_at)}</Td>
                  </Tr>
                ))}
              </tbody>
            </Table>
          )}
          <div className="flex flex-wrap items-center gap-2 px-4 py-3 text-sm text-ink-2">
            <span>{total ? `Showing ${first}–${last} of ${total.toLocaleString()}` : ' '}</span>
            <span className="flex-1" />
            <Button size="sm" disabled={page <= 1} onClick={() => setPage((p) => p - 1)}>
              Previous
            </Button>
            <Button size="sm" disabled={last >= total} onClick={() => setPage((p) => p + 1)}>
              Next
            </Button>
          </div>
        </Card>
      </PageBody>
    </AppShell>
  );
}

function waitingFor(item: InboxItem, now: number): string {
  return item.created_at_ms ? formatDuration(now - item.created_at_ms) : '';
}

function ApprovalStrip({ items }: { items: InboxItem[] }): JSX.Element {
  const now = Date.now();
  return (
    <Card tone="accent" aria-label="Needs your approval">
      <CardHeader tone="accent" title="Needs your approval" icon={<ShieldWarning size={16} />} />
      <ul className="divide-y divide-line-soft">
        {items.slice(0, 5).map((item, i) => (
          <li key={item.approval_id} className="flex flex-wrap items-center gap-x-4 gap-y-2 px-4 py-3">
            <Mono className="text-ink-2">{item.session_id.slice(0, 13)}</Mono>
            <span className="font-medium">{item.session_title || item.harness_name || 'Session'}</span>
            <Mono chip className="max-w-[420px] truncate">
              {item.prompt}
            </Mono>
            <span className="flex-1" />
            <Mono className="text-xs text-ink-3">waiting {waitingFor(item, now)}</Mono>
            <LinkButton href={`/sessions/${item.session_id}`} size="sm" variant={i === 0 ? 'primary' : 'secondary'}>
              Review
            </LinkButton>
          </li>
        ))}
      </ul>
      {items.length > 5 ? (
        <div className="border-t border-line-soft px-4 py-2.5 text-sm">
          <Link href="/approvals" className="font-medium text-accent-700">
            All {items.length} waiting approvals
          </Link>
        </div>
      ) : null}
    </Card>
  );
}
