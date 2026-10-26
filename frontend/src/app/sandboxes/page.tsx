/**
 * Sandboxes: the machines the user's sessions run commands in, and what state each is in.
 */

'use client';

import Link from 'next/link';
import { useMemo, useState } from 'react';
import { TerminalWindow } from '@phosphor-icons/react';

import { AppShell, PageBody } from '@/components/layout/AppShell';
import {
  Card,
  EmptyState,
  ErrorNote,
  LinkButton,
  Mono,
  PageHeader,
  Segmented,
  SkeletonRows,
  StatGrid,
  StatTile,
  StatusPill,
  Table,
  Td,
  Th,
  Tr,
  type StatusKind,
} from '@/components/ui';
import { getApiErrorMessage } from '@/lib/api/errors';
import { useSandboxes } from '@/lib/api/hooks/useOperate';
import type { SandboxItem, SandboxState } from '@/lib/api/types/operate.types';
import { cn, formatDuration, formatRelativeTime, formatStamp } from '@/lib/utils';

const KIND: Record<SandboxState, { kind: StatusKind; label: string }> = {
  running: { kind: 'running', label: 'Running' },
  stopped: { kind: 'idle', label: 'Stopped · idle' },
  lost: { kind: 'failed', label: 'Lost' },
  not_started: { kind: 'stopped', label: 'Not started' },
};

const CAUSE: Record<string, string> = {
  prewarm: 'Started with the run',
  prewarm_resumed: 'Restored as the run began',
  first_use: 'Started for the first tool',
  resumed: 'Restored after an idle stop',
  replaced: 'Replaced after a loss',
  adopted: 'Taken over after a restart',
};

const key = (b: SandboxItem) => `${b.session_id}/${b.hand}`;

export default function SandboxesPage(): JSX.Element {
  const [ended, setEnded] = useState<'open' | 'all'>('open');
  const { data, isLoading, error, refetch } = useSandboxes({ includeEnded: ended === 'all' });
  const [picked, setPicked] = useState<string | null>(null);
  const boxes = useMemo(() => data ?? [], [data]);
  const selected = boxes.find((b) => key(b) === picked) ?? boxes[0] ?? null;
  const counts = useMemo(() => {
    const c: Record<SandboxState, number> = { running: 0, stopped: 0, lost: 0, not_started: 0 };
    for (const b of boxes) c[b.state] += 1;
    return c;
  }, [boxes]);
  const now = Date.now();

  return (
    <AppShell>
      <PageBody>
        <PageHeader
          title="Sandboxes"
          description="Where your sessions' agents run commands. A sandbox stops when idle, keeps its files, and comes back on the next tool call."
          actions={<LinkButton href="/environments">Environments</LinkButton>}
        />
        <StatGrid>
          <StatTile label="Running" value={counts.running} />
          <StatTile label="Stopped · idle" value={counts.stopped} hint="Files saved; restarts on the next tool" />
          <StatTile label="Lost" value={counts.lost} hint={counts.lost ? 'The next tool gets a fresh one' : undefined} />
          <StatTile label="Not started" value={counts.not_started} />
        </StatGrid>

        <Segmented
          label="Which sessions"
          value={ended}
          onChange={setEnded}
          options={[
            { value: 'open', label: 'Open sessions' },
            { value: 'all', label: 'Include ended' },
          ]}
        />

        <div className="flex flex-wrap items-start gap-5">
          <Card aria-label="Sandbox list" className="min-w-0 flex-[999_1_620px]">
            {isLoading ? (
              <SkeletonRows rows={5} />
            ) : error ? (
              <div className="p-4">
                <ErrorNote message={getApiErrorMessage(error, 'Could not load sandboxes')} onRetry={() => void refetch()} />
              </div>
            ) : boxes.length === 0 ? (
              <EmptyState
                icon={<TerminalWindow size={28} />}
                title="No sandboxes yet"
                description="An agent with a sandbox gets one when a session's first tool needs it."
              />
            ) : (
              <Table minWidth={680} label="Sandboxes">
                <thead>
                  <tr>
                    <Th>Sandbox</Th>
                    <Th>State</Th>
                    <Th>Session</Th>
                    <Th>Provider</Th>
                    <Th>Since</Th>
                  </tr>
                </thead>
                <tbody>
                  {boxes.map((b) => {
                    const on = selected != null && key(b) === key(selected);
                    return (
                      <Tr key={key(b)} className={cn(on && 'bg-accent-50 shadow-[inset_3px_0_0_theme(colors.accent.500)] hover:bg-accent-50')}>
                        <Td>
                          <button type="button" onClick={() => setPicked(key(b))} aria-pressed={on} className="text-left" data-testid="sandbox-row">
                            <Mono className="text-[13px] font-medium text-ink">{b.sandbox_id ? b.sandbox_id.slice(0, 16) : b.hand}</Mono>
                            <span className="block font-mono text-xs text-ink-3">hand: {b.hand}</span>
                          </button>
                        </Td>
                        <Td>
                          <StatusPill kind={KIND[b.state].kind} label={KIND[b.state].label} />
                        </Td>
                        <Td>
                          <Link href={`/sessions/${b.session_id}`} className="text-ink hover:text-accent-700">
                            {b.session_title || `${b.harness_name ?? 'Agent'} session`}
                          </Link>
                        </Td>
                        <Td mono>{b.provider ?? '—'}</Td>
                        <Td className="whitespace-nowrap text-ink-2">{b.since_ms ? formatRelativeTime(new Date(b.since_ms)) : '—'}</Td>
                      </Tr>
                    );
                  })}
                </tbody>
              </Table>
            )}
          </Card>

          {selected ? (
            <Card as="aside" aria-label="Sandbox detail" className="flex min-w-0 flex-[1_1_360px] flex-col xl:max-w-[440px]">
              <div className="flex flex-col gap-2.5 border-b border-line-soft px-5 py-[18px]">
                <div className="flex flex-wrap items-center gap-2.5">
                  <h2 className="font-mono text-[17px] font-medium">{selected.sandbox_id ? selected.sandbox_id.slice(0, 20) : selected.hand}</h2>
                  <StatusPill kind={KIND[selected.state].kind} label={KIND[selected.state].label} />
                </div>
                <div className="text-[13px] text-ink-2">
                  Attached to{' '}
                  <Link href={`/sessions/${selected.session_id}`} className="font-mono text-[12.5px] text-ink hover:text-accent-700">
                    {selected.session_id.slice(0, 13)}
                  </Link>
                  {selected.harness_name ? ` · ${selected.harness_name}` : ''}
                </div>
                <div className="flex flex-wrap gap-2">
                  <LinkButton href={`/sessions/${selected.session_id}`} variant="primary" size="sm">
                    Open session
                  </LinkButton>
                  {selected.logs_ref ? (
                    <Mono copy={selected.logs_ref} chip className="text-xs">
                      logs: {selected.logs_ref.slice(0, 28)}
                    </Mono>
                  ) : null}
                </div>
              </div>
              <dl className="grid grid-cols-2 gap-x-4 gap-y-3.5 px-5 py-4">
                <div>
                  <dt className="text-xs text-ink-3">Provider</dt>
                  <dd className="mt-0.5 font-mono text-[12.5px]">{selected.provider ?? '—'}</dd>
                </div>
                <div>
                  <dt className="text-xs text-ink-3">Hand</dt>
                  <dd className="mt-0.5 font-mono text-[12.5px]">{selected.hand}</dd>
                </div>
                <div>
                  <dt className="text-xs text-ink-3">{selected.state === 'running' ? 'Running for' : 'Since'}</dt>
                  <dd className="mt-0.5 font-mono text-[12.5px]">
                    {selected.since_ms ? (selected.state === 'running' ? formatDuration(now - selected.since_ms) : formatStamp(selected.since_ms)) : '—'}
                  </dd>
                </div>
                <div>
                  <dt className="text-xs text-ink-3">Why</dt>
                  <dd className="mt-0.5 text-[13px]">{selected.cause ? CAUSE[selected.cause] ?? selected.cause : '—'}</dd>
                </div>
                <div>
                  <dt className="text-xs text-ink-3">Last saved</dt>
                  <dd className="mt-0.5 text-[13px]">
                    {selected.last_checkpoint_ms ? `${formatRelativeTime(new Date(selected.last_checkpoint_ms))} · ${selected.checkpoint_files} file${selected.checkpoint_files === 1 ? '' : 's'}` : 'Not yet'}
                  </dd>
                </div>
                <div>
                  <dt className="text-xs text-ink-3">Lease</dt>
                  <dd className="mt-0.5 font-mono text-[12.5px]">
                    {selected.lease_holder ? `${selected.lease_holder.slice(0, 14)}${selected.lease_expires_ms ? ` · ${formatRelativeTime(new Date(selected.lease_expires_ms))}` : ''}` : 'none'}
                  </dd>
                </div>
                {selected.reason && selected.state === 'lost' ? (
                  <div className="col-span-2">
                    <dt className="text-xs text-ink-3">What happened</dt>
                    <dd className="mt-0.5 text-[13px]">{selected.reason}</dd>
                  </div>
                ) : null}
                {selected.processes.length ? (
                  <div className="col-span-2">
                    <dt className="text-xs text-ink-3">Background processes</dt>
                    <dd className="mt-1.5 flex flex-wrap gap-1.5">
                      {selected.processes.map((p) => (
                        <Mono key={p} chip className="text-xs">
                          {p}
                        </Mono>
                      ))}
                    </dd>
                  </div>
                ) : null}
              </dl>
            </Card>
          ) : null}
        </div>
      </PageBody>
    </AppShell>
  );
}
