/**
 * Fleet (owners and admins): who serves the session queue, how busy each worker is,
 * what waits, and which worker version a deploy is on.
 */

'use client';

import { Cpu } from '@phosphor-icons/react';

import { AppShell, PageBody } from '@/components/layout/AppShell';
import { Card, CardHeader, EmptyState, ErrorNote, Meter, Mono, PageHeader, SkeletonRows, StatGrid, StatTile, StatusPill, Table, Td, Th, Tr } from '@/components/ui';
import { getApiErrorMessage } from '@/lib/api/errors';
import { useFleet } from '@/lib/api/hooks/useOperate';
import { useAuthStore } from '@/lib/store/authStore';
import { formatDuration, formatRelativeTime } from '@/lib/utils';

const HEALTH: Record<string, { kind: 'running' | 'awaiting' | 'failed'; label: string }> = {
  ok: { kind: 'running', label: 'Healthy' },
  degraded: { kind: 'awaiting', label: 'Degraded' },
  down: { kind: 'failed', label: 'Down' },
};

function forbidden(error: unknown): boolean {
  return Boolean(error && typeof error === 'object' && 'response' in error && (error as { response?: { status?: number } }).response?.status === 403);
}

export default function FleetPage(): JSX.Element {
  const role = useAuthStore((s) => s.user?.role);
  const ops = role === 'owner' || role === 'admin';
  const { workers, rollout } = useFleet(ops);
  const f = workers.data;
  const now = Date.now();

  if (!ops || forbidden(workers.error)) {
    return (
      <AppShell>
        <PageBody>
          <PageHeader title="Fleet" />
          <Card>
            <EmptyState icon={<Cpu size={28} />} title="For owners and admins" description="The worker fleet is shown to workspace owners and admins." />
          </Card>
        </PageBody>
      </AppShell>
    );
  }

  const health = f ? HEALTH[f.status] ?? { kind: 'awaiting' as const, label: f.status } : null;
  const r = rollout.data;

  return (
    <AppShell>
      <PageBody>
        <PageHeader
          title="Fleet"
          description="The workers that run sessions: whether anyone serves the queue, how many runs the fleet can carry, and what waits."
          actions={health ? <StatusPill kind={health.kind} label={health.label} className="text-base" /> : null}
        />
        {workers.error ? <ErrorNote message={getApiErrorMessage(workers.error, 'Could not reach the fleet')} onRetry={() => void workers.refetch()} /> : null}

        {f?.alerts.length ? (
          <Card tone="accent" aria-label="Alerts">
            <CardHeader tone="accent" title="Alerts" />
            <ul className="divide-y divide-line-soft">
              {f.alerts.map((a) => (
                <li key={a.code} className="flex flex-wrap items-center gap-3 px-4 py-2.5 text-sm">
                  <Mono chip>{a.code}</Mono>
                  <span>{a.message}</span>
                </li>
              ))}
            </ul>
          </Card>
        ) : null}

        <StatGrid>
          <StatTile label="Workers polling" value={f ? f.polling : '—'} hint={f ? `${f.workers.length} with heartbeats` : undefined} />
          <StatTile
            label="Run slots busy"
            value={f ? `${f.slots_busy} / ${f.slots_max}` : '—'}
            hint={f && f.slots_max ? <Meter value={f.slots_busy} max={f.slots_max} label="Slots busy" className="mt-2" /> : undefined}
          />
          <StatTile label="Waiting" value={f ? f.backlog : '—'} hint={f && f.backlog ? `oldest ${formatDuration(f.backlog_age_s * 1000)}` : 'nothing queued'} tone={f && f.backlog ? 'accent' : 'default'} />
          <StatTile label="Throughput" value={f ? `${f.dispatch_rate.toFixed(1)}/s` : '—'} hint={f ? `${f.add_rate.toFixed(1)}/s arriving · queue ${f.task_queue}` : undefined} />
        </StatGrid>

        <Card aria-label="Workers">
          <CardHeader title="Workers" description={f ? `Checked ${formatRelativeTime(new Date(f.checked_at_ms))}` : undefined} />
          {workers.isLoading ? (
            <SkeletonRows rows={3} />
          ) : !f || f.workers.length === 0 ? (
            <EmptyState title="No worker heartbeats" description="Start a worker, or check that it can reach the database." />
          ) : (
            <Table minWidth={760} label="Workers">
              <thead>
                <tr>
                  <Th>Worker</Th>
                  <Th>Version</Th>
                  <Th>Busy</Th>
                  <Th>Up for</Th>
                  <Th>Last seen</Th>
                </tr>
              </thead>
              <tbody>
                {f.workers.map((w) => (
                  <Tr key={w.worker_id}>
                    <Td>
                      <Mono className="text-[13px] font-medium text-ink">{w.host}</Mono>
                      <span className="block font-mono text-xs text-ink-3">{w.worker_id.slice(0, 18)}</span>
                    </Td>
                    <Td mono>{w.version || 'unversioned'}</Td>
                    <Td className="w-[200px]">
                      <div className="flex items-center gap-2">
                        <Meter value={w.busy} max={w.max_runs} label={`${w.host} busy`} className="flex-1" />
                        <span className="tabular font-mono text-xs">
                          {w.busy}/{w.max_runs}
                        </span>
                      </div>
                    </Td>
                    <Td mono>{formatDuration(now - w.started_at_ms)}</Td>
                    <Td className="text-ink-2">{formatRelativeTime(new Date(w.last_seen_ms))}</Td>
                  </Tr>
                ))}
              </tbody>
            </Table>
          )}
        </Card>

        <Card aria-label="Rollout">
          <CardHeader title="Rollout" description="Which worker build serves new runs; pinned runs finish on the build they started on." />
          <div className="p-4 text-sm">
            {rollout.isLoading ? (
              <SkeletonRows rows={2} />
            ) : rollout.error ? (
              <ErrorNote message={getApiErrorMessage(rollout.error, 'Could not read the rollout')} />
            ) : !r?.versioned ? (
              <p className="text-ink-2">Workers run unversioned here (no worker deployment is configured), so every worker takes every run.</p>
            ) : (
              <div className="flex flex-col gap-3">
                <div className="flex flex-wrap gap-x-6 gap-y-1">
                  <span>
                    Deployment <Mono>{r.deployment}</Mono>
                  </span>
                  <span>
                    Current <Mono>{r.current_build ?? 'none'}</Mono>
                  </span>
                  {r.ramping_build ? (
                    <span>
                      Ramping <Mono>{r.ramping_build}</Mono> at {r.ramping_percentage}%
                    </span>
                  ) : null}
                </div>
                <ul className="flex flex-wrap gap-2">
                  {(r.versions ?? []).map((v) => (
                    <li key={v.build_id}>
                      <Mono chip className="text-xs">
                        {v.build_id} · {v.status}
                        {v.drainage && v.drainage !== v.status ? ` · ${v.drainage}` : ''}
                      </Mono>
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </div>
        </Card>
      </PageBody>
    </AppShell>
  );
}
